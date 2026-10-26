"""Isolated PostgreSQL legacy migration and pg_dump/pg_restore comparison.

This script never creates/drops a database and accepts only two explicit CI
database names. The surrounding CI job creates them, performs the PostgreSQL
dump/restore, and compares the source/target semantic fingerprints.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier

from sqlalchemy import MetaData, inspect, select
from sqlalchemy.engine import make_url

from modsage.config import Settings
from modsage.database import make_database, schema_status
from modsage.jobs import enqueue_refresh
from modsage.migrations.baseline_v05 import baseline_metadata
from modsage.models import (
    Mod,
    ModEmbedding,
    ProviderCall,
    RecommendationEvent,
    RecommendationJob,
    RefreshJob,
    SourceSnapshot,
)
from modsage.provider_usage import ProviderUsageStore
from modsage.recommendation_jobs import enqueue_recommendation_job, get_recommendation_job
from modsage.schema import HEAD_REVISION, _inspect_schema

FIXTURE_ID = "7000000000000000002"
FIXTURE_TOKEN = "postgres-restore-fixture-token-" + "x" * 32


def _json_default(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("Unexpected fixture data type")


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default
    )


def _table_fingerprints(engine) -> dict:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    result = {}
    with engine.connect() as connection:
        for name, table in sorted(metadata.tables.items()):
            columns = sorted(table.columns.keys())
            rows = sorted(
                _canonical(list(row))
                for row in connection.execute(select(*(table.c[column] for column in columns)))
            )
            content = _canonical({"columns": columns, "rows": rows}).encode("utf-8")
            result[name] = {"rows": len(rows), "sha256": hashlib.sha256(content).hexdigest()}
    return result


def _seed_fixture(factory, settings):
    with factory.begin() as session:
        session.add(
            Mod(
                id=FIXTURE_ID,
                title="TEST FIXTURE: PostgreSQL restore check",
                description="Synthetic CI record; never imported into the application catalog.",
                summary_zh="仅用于隔离数据库迁移与恢复验收的测试数据。",
                source_url="https://example.invalid/postgres-restore-fixture",
                content_hash="a" * 64,
                evidence=[{"fixture": True}],
            )
        )
        session.flush()
        session.add(
            SourceSnapshot(
                mod_id=FIXTURE_ID,
                payload_hash="b" * 64,
                raw={"test_fixture": True, "preserve": ["source", "中文", {"nested": 1}]},
            )
        )
        session.add(
            ModEmbedding(
                mod_id=FIXTURE_ID,
                namespace="c" * 64,
                document_hash="d" * 64,
                model="postgres-restore-fixture-model",
                dimensions=3,
                vector=[0.25, -0.75, 0.5],
            )
        )
    enqueue_recommendation_job(
        factory,
        settings,
        "TEST FIXTURE: restoration preserves queued work and its events",
        token=FIXTURE_TOKEN,
        idempotency_key="postgres-restore-fixture-request-" + "y" * 32,
    )
    enqueue_refresh(factory, settings, idempotency_key="postgres-restore-fixture", source="public")
    usage_settings = settings.model_copy(
        update={
            "llm_api_key": "test-fixture-key-never-sent",
            "llm_base_url": "https://example.invalid/v1",
            "llm_model": "postgres-restore-fixture-model",
            "provider_calls_enabled": True,
            "provider_daily_call_limit": 2,
        }
    )
    # Database admission only: no model client or network transport is constructed.
    store = ProviderUsageStore(factory, usage_settings)
    failed = store.reserve("llm", "understand")
    store.finish(failed, status="failed", latency_ms=1, error_code="timeout")
    store.reserve("llm", "understand")


def _concurrent_adopt() -> list[str]:
    barrier = Barrier(4)
    source = """
import json
import os
from modsage.database import init_database, make_database
engine, _ = make_database(os.environ['MODSAGE_DATABASE_URL'])
try:
    print(json.dumps(init_database(engine)))
finally:
    engine.dispose()
"""

    def adopt(_):
        barrier.wait(timeout=10)
        # Separate processes exercise PostgreSQL advisory locking rather than just
        # the in-process lock protecting Alembic's module-level environment.
        result = subprocess.run(
            [sys.executable, "-c", source],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode:
            raise RuntimeError("Concurrent legacy migration process failed")
        report = json.loads(result.stdout)
        assert report["status"] == "current"
        assert report["current_revision"] == HEAD_REVISION
        return report["action"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        actions = sorted(pool.map(adopt, range(4)))
    assert actions == ["adopted_legacy", "unchanged", "unchanged", "unchanged"]
    return actions


def _assert_fixture_state(factory):
    with factory() as session:
        assert session.scalars(select(Mod.id)).all() == [FIXTURE_ID]
        assert session.scalars(select(SourceSnapshot.mod_id)).all() == [FIXTURE_ID]
        vector = session.scalars(select(ModEmbedding)).one()
        assert vector.vector == [0.25, -0.75, 0.5]
        job = session.scalars(select(RecommendationJob)).one()
        assert job.status == "queued" and job.attempts == 0
        assert session.scalars(select(RecommendationEvent.sequence)).all() == [1]
        refresh = session.scalars(select(RefreshJob)).one()
        assert refresh.status == "queued" and refresh.attempts == 0
        assert sorted(session.scalars(select(ProviderCall.status)).all()) == ["failed", "started"]
    assert get_recommendation_job(factory, job.id, FIXTURE_TOKEN)["status"] == "queued"


def _verify_postgres_drift_guards(engine) -> list[str]:
    cases = [
        (
            "partial_index",
            [
                "DROP INDEX ix_mods_category",
                "CREATE INDEX ix_mods_category ON mods(category) WHERE category='units'",
            ],
            "index_definition_mismatch",
        ),
        (
            "materialized_view",
            ["CREATE MATERIALIZED VIEW fixture_view AS SELECT id FROM mods"],
            "unexpected_materialized_views",
        ),
        (
            "user_trigger",
            [
                "CREATE FUNCTION fixture_trigger() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NEW; END; $$",
                "CREATE TRIGGER fixture_trigger BEFORE INSERT ON mods FOR EACH ROW "
                "EXECUTE FUNCTION fixture_trigger()",
            ],
            "unexpected_triggers",
        ),
        (
            "unbounded_varchar",
            ["ALTER TABLE recommendation_jobs ALTER COLUMN idempotency_key TYPE varchar"],
            "column_type_mismatch",
        ),
        (
            "unknown_revision",
            ["UPDATE alembic_version SET version_num='fixture_future_version'"],
            "unknown_revision",
        ),
        (
            "missing_serial_default",
            ["ALTER TABLE source_snapshots ALTER COLUMN id DROP DEFAULT"],
            "autoincrement_default_mismatch",
        ),
        (
            "repointed_serial_default",
            [
                "ALTER TABLE source_snapshots ALTER COLUMN id "
                "SET DEFAULT nextval('metric_snapshots_id_seq'::regclass)"
            ],
            "autoincrement_default_mismatch",
        ),
    ]
    for _, statements, expected in cases:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                for statement in statements:
                    connection.exec_driver_sql(statement)
                report = _inspect_schema(connection)
                assert report["status"] == "blocked" and expected in report["issues"]
            finally:
                transaction.rollback()
    assert schema_status(engine)["status"] == "current"
    return [name for name, _, _ in cases]


def check_roundtrip(*, prepare: bool) -> dict:
    expected_database = "modsage_restore_source" if prepare else "modsage_restore_target"
    database_url = os.environ.get("MODSAGE_DATABASE_URL", "")
    parsed = make_url(database_url)
    if (
        os.environ.get("MODSAGE_RESTORE_SMOKE") != "1"
        or parsed.get_backend_name() != "postgresql"
        or parsed.database != expected_database
    ):
        raise ValueError("Only explicitly enabled, named PostgreSQL CI databases are accepted")
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        steam_api_key="",
        llm_api_key="",
        llm_base_url="",
        llm_model="",
        embedding_api_key="",
        embedding_base_url="",
        embedding_model="",
        provider_calls_enabled=False,
        recommendation_worker_enabled=False,
        refresh_enabled=False,
    )
    engine, factory = make_database(database_url)
    try:
        actions = []
        drift_checks = []
        if prepare:
            assert not inspect(engine).get_table_names(), "CI source database must be empty"
            assert not inspect(engine).get_view_names(), "CI source schema must be empty"
            baseline_metadata().create_all(engine, checkfirst=False)
            _seed_fixture(factory, settings)
            before = _table_fingerprints(engine)
            assert schema_status(engine)["status"] == "legacy"
            actions = _concurrent_adopt()
            after = _table_fingerprints(engine)
            assert before == {k: v for k, v in after.items() if k != "alembic_version"}
            drift_checks = _verify_postgres_drift_guards(engine)
            assert after == _table_fingerprints(engine), "Drift probes changed stored content"
        report = schema_status(engine)
        assert report["status"] == "current"
        _assert_fixture_state(factory)
        return {
            "status": "passed",
            "scope": "isolated_postgresql_migration_restore",
            "data": "explicit_test_fixtures_only",
            "provider_calls": 0,
            "external_source_calls": 0,
            "migration_actions": actions,
            "schema_drift_checks": drift_checks,
            "legacy_contents_preserved": True if prepare else None,
            "fingerprint": {
                "schema_revision": report["current_revision"],
                "tables": _table_fingerprints(engine),
            },
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    try:
        result = check_roundtrip(prepare=args.prepare)
    except Exception:
        # Assertions/DB exceptions can contain row values or connection details.
        print(json.dumps({"status": "failed", "error_code": "postgres_restore_check_failed"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
