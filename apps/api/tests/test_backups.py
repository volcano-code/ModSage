import hashlib
import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select

from modsage import backups
from modsage.backups import BackupError, backup_sqlite, restore_sqlite
from modsage.database import make_database
from modsage.digests import build_digest
from modsage.ingestion import import_items
from modsage.jobs import enqueue_refresh
from modsage.models import Mod, ModEmbedding, ProviderCall
from modsage.provider_usage import ProviderLimitExceeded, ProviderUsageStore, usage_report
from modsage.recommendation_jobs import enqueue_recommendation_job, get_recommendation_job


def snapshot_rows(path):
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


def test_backup_restore_preserves_data_jobs_events_vectors_and_used_allowance(
    database, settings, item_factory, tmp_path
):
    source = tmp_path / "test.db"
    with database.begin() as session:
        import_items(
            session,
            [item_factory(description="PRIVATE SOURCE FIXTURE", required_ids=["10002"])],
        )
        session.add(
            ModEmbedding(
                mod_id="10001",
                namespace="n" * 64,
                document_hash="d" * 64,
                model="fixture-embedding",
                dimensions=2,
                vector=[0.25, 0.75],
            )
        )
        build_digest(session)
    token = "private-fixture-token-" + "x" * 32
    job = enqueue_recommendation_job(
        database,
        settings,
        "PRIVATE QUERY FIXTURE",
        token=token,
        idempotency_key="private-fixture-idempotency-" + "y" * 32,
    )
    enqueue_refresh(database, settings, idempotency_key="fixture-refresh", source="public")
    configured = settings.model_copy(
        update={
            "llm_api_key": "private-fixture-credential",
            "llm_model": "fixture-model",
            "provider_daily_call_limit": 2,
        }
    )
    store = ProviderUsageStore(database, configured)
    failed = store.reserve("llm", "understand")
    store.finish(failed, status="failed", latency_ms=10, error_code="timeout")
    unknown = store.reserve("llm", "understand")
    before = snapshot_rows(source)
    source_bytes = source.read_bytes()

    backup_path = tmp_path / "backup.db"
    report = backup_sqlite(settings.database_url, backup_path)
    assert report["status"] == "succeeded"
    assert report["schema"]["status"] == "current"
    assert report["network_calls"] == 0
    assert report["table_counts"]["mods"] == 1
    assert report["table_counts"]["recommendation_events"] == 1
    assert report["table_counts"]["provider_calls"] == 2
    assert report["sha256"] == hashlib.sha256(backup_path.read_bytes()).hexdigest()
    assert report["bytes"] == backup_path.stat().st_size
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert source.read_bytes() == source_bytes
    assert snapshot_rows(backup_path) == before

    restored = tmp_path / "restored.db"
    restore = restore_sqlite(backup_path, restored)
    assert restore["schema"] == report["schema"]
    assert restore["table_counts"] == report["table_counts"]
    assert restore["integrity_check"] == restore["foreign_key_check"] == "ok"
    assert "restored_usage_is_a_snapshot_not_the_provider_billing_balance" in restore["warnings"]
    assert snapshot_rows(restored) == before
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600

    restored_engine, restored_factory = make_database(f"sqlite:///{restored}")
    try:
        assert get_recommendation_job(restored_factory, job["id"], token) == job
        assert usage_report(restored_factory, configured)["today"]["reserved_calls"] == 2
        with restored_factory() as session:
            assert session.get(ProviderCall, unknown).status == "started"
        with pytest.raises(ProviderLimitExceeded, match="daily_call_limit"):
            ProviderUsageStore(restored_factory, configured).reserve("llm", "understand")
    finally:
        restored_engine.dispose()

    encoded = json.dumps([report, restore])
    for private in (
        "PRIVATE SOURCE FIXTURE",
        "PRIVATE QUERY FIXTURE",
        token,
        "private-fixture-credential",
        settings.database_url,
    ):
        assert private not in encoded
    assert not list(tmp_path.glob(".modsage-snapshot-*"))


def test_wal_changes_are_copied_and_output_is_a_standalone_file(
    database, settings, item_factory, tmp_path
):
    with database.begin() as session:
        import_items(session, [item_factory()])
    source = tmp_path / "test.db"
    # Keep this connection open so the last committed change remains in the WAL.
    with sqlite3.connect(source) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE mods SET title='WAL-ONLY FIXTURE' WHERE id='10001'")
        writer.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        backup = tmp_path / "wal-backup.db"
        backup_sqlite(settings.database_url, backup)
        # No companion WAL/SHM is required to open the backup read-only.
        with sqlite3.connect(backup.as_uri() + "?mode=ro&immutable=1", uri=True) as copied:
            assert copied.execute("SELECT title FROM mods").fetchone()[0] == "WAL-ONLY FIXTURE"
            assert copied.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert not Path(str(backup) + "-wal").exists()
        assert not Path(str(backup) + "-shm").exists()


def test_verified_legacy_database_is_not_stamped_or_upgraded(database, settings, tmp_path):
    source = tmp_path / "test.db"
    with sqlite3.connect(source) as connection:
        connection.execute("DROP TABLE alembic_version")
    original = source.read_bytes()
    report = backup_sqlite(settings.database_url, tmp_path / "legacy-backup.db")
    assert report["schema"]["status"] == "legacy"
    assert report["schema"]["current_revision"] is None
    assert "legacy_schema_preserved_explicit_upgrade_required" in report["warnings"]
    restored = tmp_path / "legacy-restored.db"
    assert restore_sqlite(tmp_path / "legacy-backup.db", restored)["schema"]["status"] == "legacy"
    assert source.read_bytes() == original
    assert "alembic_version" not in snapshot_rows(restored)


def test_existing_destination_and_same_path_are_never_overwritten(database, settings, tmp_path):
    source = tmp_path / "test.db"
    original = source.read_bytes()
    with pytest.raises(BackupError) as same:
        backup_sqlite(settings.database_url, source)
    assert same.value.code == "same_path"
    target = tmp_path / "existing.db"
    target.write_bytes(b"preserve this existing file")
    with pytest.raises(BackupError) as existing:
        backup_sqlite(settings.database_url, target)
    assert existing.value.code == "destination_exists"
    assert target.read_bytes() == b"preserve this existing file"
    with pytest.raises(BackupError):
        restore_sqlite(source, target)
    assert source.read_bytes() == original


@pytest.mark.parametrize("which", ["source", "destination", "parent"])
def test_symlinks_are_refused_without_following_them(database, settings, tmp_path, which):
    source = tmp_path / "test.db"
    target = tmp_path / "backup.db"
    if which == "source":
        link = tmp_path / "source-link.db"
        link.symlink_to(source)
        source = link
    elif which == "destination":
        target.symlink_to(source)
    else:
        directory = tmp_path / "actual"
        directory.mkdir()
        link = tmp_path / "parent-link"
        link.symlink_to(directory, target_is_directory=True)
        target = link / "backup.db"
    before = (tmp_path / "test.db").read_bytes()
    with pytest.raises(BackupError) as error:
        restore_sqlite(source, target)
    assert error.value.code == "symlink_path"
    assert (tmp_path / "test.db").read_bytes() == before


def test_missing_source_does_not_create_an_empty_database(tmp_path):
    source = tmp_path / "absent.db"
    with pytest.raises(BackupError) as error:
        backup_sqlite(f"sqlite:///{source}", tmp_path / "backup.db")
    assert error.value.code == "source_missing"
    assert list(tmp_path.iterdir()) == []


def test_missing_destination_directory_is_not_created(database, settings, tmp_path):
    with pytest.raises(BackupError) as error:
        backup_sqlite(settings.database_url, tmp_path / "missing" / "backup.db")
    assert error.value.code == "destination_parent_missing"
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_stale_destination_companions_are_not_reused(database, settings, tmp_path, suffix):
    target = tmp_path / "new.db"
    stale = Path(str(target) + suffix)
    stale.write_bytes(b"preserve existing journal")
    with pytest.raises(BackupError) as error:
        backup_sqlite(settings.database_url, target)
    assert error.value.code == "destination_companions_exist"
    assert stale.read_bytes() == b"preserve existing journal"
    assert not target.exists()


def test_parent_traversal_cannot_hide_a_symlink(database, settings, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(BackupError) as error:
        backup_sqlite(settings.database_url, linked / ".." / "backup.db")
    assert error.value.code == "symlink_path"
    assert not (tmp_path / "backup.db").exists()


def test_non_regular_source_is_refused_before_opening(tmp_path):
    fifo = tmp_path / "pipe.db"
    os.mkfifo(fifo)
    with pytest.raises(BackupError) as error:
        restore_sqlite(fifo, tmp_path / "backup.db")
    assert error.value.code == "source_not_file"


@pytest.mark.parametrize(
    "database_url,code",
    [
        ("postgresql://fixture-user:private-password@db/private-name", "unsupported_database"),
        ("sqlite://", "file_database_required"),
        ("sqlite:///:memory:", "file_database_required"),
        ("sqlite:///file:memory?mode=memory&uri=true", "file_database_required"),
        ("not-a-database-url-private-password", "invalid_database_url"),
    ],
)
def test_unsupported_database_errors_do_not_echo_configuration(tmp_path, database_url, code):
    with pytest.raises(BackupError) as error:
        backup_sqlite(database_url, tmp_path / "backup.db")
    assert error.value.code == code
    assert "private-password" not in str(error.value)
    assert database_url not in str(error.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["corrupt", "unrelated", "drift", "future", "foreign_key"])
def test_invalid_sources_are_never_published(database, settings, tmp_path, kind):
    source = tmp_path / "test.db"
    if kind == "corrupt":
        source = tmp_path / "corrupt.db"
        source.write_bytes(b"not a sqlite database, private fixture data")
    else:
        with sqlite3.connect(source) as connection:
            if kind == "unrelated":
                connection.execute("CREATE TABLE private_unknown_table (value TEXT)")
            elif kind == "drift":
                connection.execute("DROP TABLE digests")
            elif kind == "future":
                connection.execute(
                    "UPDATE alembic_version SET version_num='unknown-future-fixture'"
                )
            else:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute(
                    "INSERT INTO source_snapshots (mod_id,payload_hash,observed_at,raw) VALUES (?,?,?,?)",
                    ("missing-fixture", "x" * 64, "2026-10-26 00:00:00", "{}"),
                )
    target = tmp_path / "invalid-copy.db"
    original = source.read_bytes()
    with pytest.raises(BackupError) as error:
        restore_sqlite(source, target)
    expected = {
        "corrupt": "invalid_database",
        "unrelated": "schema_not_supported",
        "drift": "schema_not_supported",
        "future": "schema_not_supported",
        "foreign_key": "foreign_key_check_failed",
    }
    assert error.value.code == expected[kind]
    assert not target.exists()
    assert not list(tmp_path.glob(".modsage-snapshot-*"))
    assert source.read_bytes() == original
    assert "private" not in str(error.value)


def test_atomic_publication_rejects_a_destination_race(database, settings, tmp_path, monkeypatch):
    target = tmp_path / "race.db"
    original_link = os.link

    def race(source, destination, **kwargs):
        target.write_bytes(b"another operation won")
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(backups.os, "link", race)
    with pytest.raises(BackupError) as error:
        backup_sqlite(settings.database_url, target)
    assert error.value.code == "destination_exists"
    assert target.read_bytes() == b"another operation won"
    assert not list(tmp_path.glob(".modsage-snapshot-*"))


def test_directory_sync_failure_reports_existing_verified_file(
    database, settings, tmp_path, monkeypatch
):
    target = tmp_path / "published.db"
    original_fsync = os.fsync

    def fail_directory_sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("private filesystem diagnostic")
        return original_fsync(descriptor)

    monkeypatch.setattr(backups.os, "fsync", fail_directory_sync)
    with pytest.raises(BackupError) as error:
        backup_sqlite(settings.database_url, target)
    assert error.value.code == "publication_durability_unknown"
    assert "private filesystem diagnostic" not in str(error.value)
    assert target.is_file()
    assert snapshot_rows(target) == snapshot_rows(tmp_path / "test.db")
    assert not list(tmp_path.glob(".modsage-snapshot-*"))


def test_simultaneous_backups_publish_exactly_one_destination(database, settings, tmp_path):
    target = tmp_path / "concurrent.db"

    def run(_):
        try:
            return backup_sqlite(settings.database_url, target)["status"]
        except BackupError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(run, range(4)))
    assert outcomes.count("succeeded") == 1
    assert outcomes.count("destination_exists") == 3
    assert snapshot_rows(target) == snapshot_rows(tmp_path / "test.db")
    assert not list(tmp_path.glob(".modsage-snapshot-*"))


def test_exclusive_source_lock_times_out_without_publishing(database, settings, tmp_path):
    source = tmp_path / "test.db"
    with sqlite3.connect(source) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(BackupError) as error:
                backup_sqlite(settings.database_url, tmp_path / "blocked.db", timeout_seconds=0.05)
            assert error.value.code == "snapshot_timeout"
        finally:
            writer.rollback()
    assert not (tmp_path / "blocked.db").exists()
    assert not list(tmp_path.glob(".modsage-snapshot-*"))


def test_engine_input_can_be_used_without_closing_the_application_engine(
    database, settings, item_factory, tmp_path
):
    with database.begin() as session:
        import_items(session, [item_factory()])
        engine = session.get_bind()
    report = backup_sqlite(engine, tmp_path / "engine-backup.db")
    assert report["table_counts"]["mods"] == 1
    with database() as session:
        assert session.scalar(select(Mod.id)) == "10001"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "60", 601])
def test_invalid_budget_is_rejected_without_creating_files(database, settings, tmp_path, timeout):
    target = tmp_path / "invalid-budget.db"
    with pytest.raises(BackupError) as error:
        backup_sqlite(settings.database_url, target, timeout_seconds=timeout)
    assert error.value.code == "invalid_timeout"
    assert not target.exists()
