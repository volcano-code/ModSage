"""Exercise a real SQLite backup/restore using explicitly isolated CI fixture data."""

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from modsage.backups import backup_sqlite, restore_sqlite
from modsage.config import Settings
from modsage.database import init_database, make_database
from modsage.jobs import enqueue_refresh
from modsage.models import Mod, ModEmbedding, ProviderCall, SourceSnapshot
from modsage.provider_usage import ProviderLimitExceeded, ProviderUsageStore, usage_report
from modsage.recommendation_jobs import enqueue_recommendation_job, get_recommendation_job


def _rows(path):
    with sqlite3.connect(path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
            for name in names
        }


def check_roundtrip() -> dict:
    with TemporaryDirectory(prefix="modsage-restore-fixture-") as directory:
        root = Path(directory)
        source = root / "isolated-source.db"
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{source}",
            llm_api_key="",
            llm_base_url="",
            llm_model="",
            embedding_api_key="",
            embedding_base_url="",
            embedding_model="",
            steam_api_key="",
            provider_calls_enabled=False,
            recommendation_worker_enabled=False,
            refresh_enabled=False,
        )
        engine, factory = make_database(settings.database_url)
        restored_engine = None
        try:
            init_database(engine)
            with factory.begin() as session:
                session.add(
                    Mod(
                        id="7000000000000000001",
                        title="TEST FIXTURE: SQLite restore check",
                        description="Only stored in an isolated temporary database; not a real Mod.",
                        source_url="https://example.invalid/restore-fixture",
                        content_hash="a" * 64,
                    )
                )
                session.flush()
                session.add(
                    SourceSnapshot(
                        mod_id="7000000000000000001",
                        payload_hash="b" * 64,
                        raw={"test_fixture": True, "private_payload": "preserve exact contents"},
                    )
                )
                session.add(
                    ModEmbedding(
                        mod_id="7000000000000000001",
                        namespace="c" * 64,
                        document_hash="d" * 64,
                        model="restore-fixture-model",
                        dimensions=2,
                        vector=[0.25, 0.75],
                    )
                )
            token = "restore-fixture-token-" + "x" * 32
            job = enqueue_recommendation_job(
                factory,
                settings,
                "TEST FIXTURE: queued work must remain queued",
                token=token,
                idempotency_key="restore-fixture-request-" + "y" * 32,
            )
            enqueue_refresh(factory, settings, idempotency_key="restore-fixture", source="public")
            usage_settings = settings.model_copy(
                update={
                    "llm_api_key": "test-fixture-key-never-sent",
                    "llm_base_url": "https://example.invalid/v1",
                    "llm_model": "restore-fixture-model",
                    "provider_calls_enabled": True,
                    "provider_daily_call_limit": 2,
                }
            )
            # Admission/finish are database operations only. No provider client is created.
            usage = ProviderUsageStore(factory, usage_settings)
            failed = usage.reserve("llm", "understand")
            usage.finish(failed, status="failed", latency_ms=1, error_code="timeout")
            unknown = usage.reserve("llm", "understand")
            original = _rows(source)
            original_bytes = source.read_bytes()

            backup_path, restored_path = root / "backup.db", root / "restore.db"
            backup = backup_sqlite(engine, backup_path)
            restored = restore_sqlite(backup_path, restored_path)
            assert original == _rows(backup_path) == _rows(restored_path)
            assert source.read_bytes() == original_bytes, "Source file was modified"
            restored_engine, restored_factory = make_database(f"sqlite:///{restored_path}")
            assert get_recommendation_job(restored_factory, job["id"], token) == job
            report = usage_report(restored_factory, usage_settings)
            assert report["today"]["reserved_calls"] == 2
            with restored_factory() as session:
                assert session.get(ProviderCall, unknown).status == "started"
            try:
                ProviderUsageStore(restored_factory, usage_settings).reserve("llm", "understand")
            except ProviderLimitExceeded as error:
                assert error.code == "daily_call_limit"
            else:
                raise AssertionError("The restore refunded a consumed daily allowance")
            return {
                "status": "passed",
                "scope": "isolated_sqlite_backup_restore",
                "data": "explicit_test_fixtures_only",
                "all_table_contents_equal": True,
                "source_unchanged": True,
                "queued_job_and_events_preserved": True,
                "failed_and_unknown_usage_still_consumed": True,
                "network_calls": 0,
                "provider_calls": 0,
                "backup": backup,
                "restore": restored,
            }
        finally:
            if restored_engine is not None:
                restored_engine.dispose()
            engine.dispose()


if __name__ == "__main__":
    print(json.dumps(check_roundtrip(), ensure_ascii=False, indent=2))
