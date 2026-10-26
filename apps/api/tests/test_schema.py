import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text

from modsage.database import (
    Base,
    SchemaMigrationError,
    init_database,
    make_database,
    schema_status,
)
from modsage.migrations.baseline_v05 import baseline_metadata
from modsage.models import Mod, ProviderCall
from modsage.schema import HEAD_REVISION, _alembic_config


def _initialize_process(url):
    engine, _ = make_database(url)
    try:
        return init_database(engine)
    finally:
        engine.dispose()


def test_empty_status_does_not_create_sqlite_file_or_directory(tmp_path):
    target = tmp_path / "absent" / "catalog.db"
    engine = create_engine(f"sqlite:///{target}")
    report = schema_status(engine)
    assert report["status"] == "empty"
    assert report["current_revision"] is None
    assert report["head_revision"] == HEAD_REVISION
    assert not target.parent.exists()
    engine.dispose()


def test_empty_database_creation_is_versioned_and_idempotent(tmp_path):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'catalog.db'}")
    assert init_database(engine)["action"] == "created"
    assert init_database(engine)["action"] == "unchanged"
    assert schema_status(engine)["status"] == "current"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).all() == [
            (HEAD_REVISION,)
        ]
    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    engine.dispose()


def test_memory_database_supported():
    engine, _ = make_database("sqlite://")
    assert schema_status(engine)["status"] == "empty"
    assert init_database(engine)["status"] == "current"
    assert schema_status(engine)["status"] == "current"
    engine.dispose()


def test_sqlite_url_tilde_is_a_literal_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine, _ = make_database("sqlite:///~/literal.db")
    assert (tmp_path / "~").is_dir()
    assert schema_status(engine)["status"] == "empty"
    init_database(engine)
    assert schema_status(engine)["status"] == "current"
    assert (tmp_path / "~" / "literal.db").is_file()
    engine.dispose()


@pytest.mark.parametrize("flag", ["true", "TRUE", "yes", "on", "1", "false"])
def test_sqlite_uri_options_are_rejected_consistently_without_connecting(tmp_path, flag):
    engine = create_engine(f"sqlite:///{tmp_path / 'never-created.db'}?uri={flag}")
    assert schema_status(engine)["issues"] == ["unsupported_sqlite_uri"]
    with pytest.raises(SchemaMigrationError, match="unsupported_sqlite_uri"):
        init_database(engine)
    assert not (tmp_path / "never-created.db").exists()
    engine.dispose()


def test_legacy_v05_adoption_preserves_source_and_consumed_budget(tmp_path):
    engine, factory = make_database(f"sqlite:///{tmp_path / 'legacy.db'}")
    # This is deliberately the pre-Alembic v0.5 creation path.
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(
            Mod(
                id="100001",
                title="Migration fixture",
                description="Explicit test fixture; not a Workshop import",
                source_url="https://steamcommunity.com/sharedfiles/filedetails/?id=100001",
                content_hash="fixture-hash",
                summary_zh="迁移测试记录",
                evidence=[{"source": "fixture"}],
            )
        )
        session.add(
            ProviderCall(
                id="legacy-spent-attempt",
                provider="llm",
                operation="understand",
                configuration_id="fixture",
                status="started",
                reserved_at=now,
            )
        )
    assert schema_status(engine)["status"] == "legacy"
    assert "alembic_version" not in inspect(engine).get_table_names()
    assert init_database(engine)["action"] == "adopted_legacy"
    with factory() as session:
        mod = session.get(Mod, "100001")
        assert mod.summary_zh == "迁移测试记录"
        assert mod.evidence == [{"source": "fixture"}]
        call = session.get(ProviderCall, "legacy-spent-attempt")
        assert call.status == "started"
        assert call.completed_at is None
    assert init_database(engine)["action"] == "unchanged"
    engine.dispose()


def test_schema_status_is_read_only_for_existing_legacy(tmp_path):
    path = tmp_path / "legacy.db"
    engine, _ = make_database(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    before = path.read_bytes()
    assert schema_status(engine)["status"] == "legacy"
    assert path.read_bytes() == before
    engine.dispose()


@pytest.mark.parametrize("version", ["9999_future_revision", "private-value-without-echo"])
def test_unknown_revision_is_rejected_without_modification(tmp_path, version):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'future.db'}")
    init_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num=:value"), {"value": version}
        )
    report = schema_status(engine)
    assert report["status"] == "blocked"
    assert report["issues"] == ["unknown_revision"]
    assert version not in str(report)
    with pytest.raises(SchemaMigrationError, match="unknown_revision"):
        init_database(engine)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == version
        )
    engine.dispose()


@pytest.mark.parametrize(
    ("statement", "issue"),
    [
        ("DROP TABLE provider_calls", "missing_tables"),
        ("CREATE TABLE unexpected (id INTEGER)", "unexpected_tables"),
        ("ALTER TABLE mods ADD COLUMN surprise TEXT", "unexpected_columns"),
        ("ALTER TABLE mods ADD COLUMN alembic_version TEXT", "unexpected_columns"),
        ("ALTER TABLE mods DROP COLUMN title", "missing_columns"),
        ("DROP INDEX ix_mods_category", "missing_indexes"),
        ("CREATE INDEX unexpected_index ON mods(title)", "unexpected_indexes"),
        ("CREATE VIEW unexpected AS SELECT id FROM mods", "unexpected_views"),
        (
            "CREATE TRIGGER surprise AFTER INSERT ON mods BEGIN DELETE FROM mods; END",
            "unexpected_triggers",
        ),
    ],
)
def test_schema_drift_is_reported_not_auto_repaired(tmp_path, statement, issue):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'drift.db'}")
    init_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(statement)
    before_tables = inspect(engine).get_table_names()
    assert issue in schema_status(engine)["issues"]
    with pytest.raises(SchemaMigrationError):
        init_database(engine)
    assert inspect(engine).get_table_names() == before_tables
    engine.dispose()


@pytest.mark.parametrize(
    "definition",
    ["category DESC", "category COLLATE NOCASE", "category) WHERE category='units' --"],
)
def test_index_order_collation_and_partial_predicate_are_not_silently_adopted(tmp_path, definition):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'index-drift.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_mods_category")
        connection.exec_driver_sql(f"CREATE INDEX ix_mods_category ON mods({definition})")
    assert "index_definition_mismatch" in schema_status(engine)["issues"]
    with pytest.raises(SchemaMigrationError):
        init_database(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()
    engine.dispose()


def test_unexpected_expression_index_is_detected_even_when_alembic_skips_it(tmp_path):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'expression-index.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE INDEX surprise ON mods(lower(title))")
    assert "unexpected_indexes" in schema_status(engine)["issues"]
    with pytest.raises(SchemaMigrationError):
        init_database(engine)
    engine.dispose()


@pytest.mark.parametrize(
    "alteration", ["primary_key", "nullable", "type", "length", "unique", "foreign_key"]
)
def test_legacy_constraints_and_column_contract_are_verified(tmp_path, alteration):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'legacy-drift.db'}")
    metadata = baseline_metadata()
    if alteration == "primary_key":
        # Losing one part of the composite PK is missed by Alembic autogenerate alone.
        from sqlalchemy import PrimaryKeyConstraint

        table = metadata.tables["mod_embeddings"]
        table.c.namespace.primary_key = False
        table.constraints.remove(table.primary_key)
        table.append_constraint(PrimaryKeyConstraint(table.c.mod_id))
    elif alteration == "nullable":
        metadata.tables["mods"].c.title.nullable = True
    elif alteration == "type":
        from sqlalchemy import Integer

        metadata.tables["mods"].c.title.type = Integer()
    elif alteration == "length":
        from sqlalchemy import String

        metadata.tables["recommendation_jobs"].c.idempotency_key.type = String()
    elif alteration == "unique":
        from sqlalchemy import UniqueConstraint

        table = metadata.tables["source_snapshots"]
        table.constraints = {x for x in table.constraints if not isinstance(x, UniqueConstraint)}
    else:
        from sqlalchemy import ForeignKeyConstraint

        table = metadata.tables["mod_embeddings"]
        table.constraints = {
            x for x in table.constraints if not isinstance(x, ForeignKeyConstraint)
        }
    metadata.create_all(engine)
    assert schema_status(engine)["status"] == "blocked"
    with pytest.raises(SchemaMigrationError):
        init_database(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()
    engine.dispose()


def test_computed_column_cannot_masquerade_as_an_ordinary_source_field(tmp_path):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'computed.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE mods DROP COLUMN title")
        connection.exec_driver_sql(
            "ALTER TABLE mods ADD COLUMN title TEXT GENERATED ALWAYS AS ('fixture') VIRTUAL NOT NULL"
        )
    assert "unexpected_generated_column" in schema_status(engine)["issues"]
    with pytest.raises(SchemaMigrationError):
        init_database(engine)
    engine.dispose()


def test_invalid_version_table_cannot_be_adopted(tmp_path):
    engine, _ = make_database(f"sqlite:///{tmp_path / 'invalid-version.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:value)"), {"value": HEAD_REVISION}
        )
    assert schema_status(engine)["issues"] == ["invalid_revision_table"]
    with pytest.raises(SchemaMigrationError, match="invalid_revision_table"):
        init_database(engine)
    engine.dispose()


def test_concurrent_process_initialization_does_not_race(tmp_path):
    url = f"sqlite:///{tmp_path / 'concurrent.db'}"
    with ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        results = list(pool.map(_initialize_process, [url] * 3))
    assert [x["action"] for x in results].count("created") == 1
    assert all(x["current_revision"] == HEAD_REVISION for x in results)


def test_sqlite_migration_lock_has_bounded_wait(tmp_path, monkeypatch):
    from modsage import schema

    monkeypatch.setattr(schema, "LOCK_TIMEOUT_SECONDS", 0.1)
    path = tmp_path / "locked.db"
    engine, _ = make_database(f"sqlite:///{path}")
    with sqlite3.connect(path) as holder:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(SchemaMigrationError, match="migration_lock_timeout"):
            init_database(engine)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_failed_verification_rolls_back_schema_creation(tmp_path, monkeypatch):
    from modsage import schema

    engine, _ = make_database(f"sqlite:///{tmp_path / 'rollback.db'}")
    original = schema._inspect_schema
    calls = 0

    def fail_second(connection):
        nonlocal calls
        calls += 1
        if calls == 2:
            return {"status": "blocked"}
        return original(connection)

    monkeypatch.setattr(schema, "_inspect_schema", fail_second)
    with pytest.raises(SchemaMigrationError, match="migration_verification_failed"):
        init_database(engine)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_destructive_downgrade_refused(tmp_path):
    engine, factory = make_database(f"sqlite:///{tmp_path / 'keep.db'}")
    init_database(engine)
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="Destructive downgrade"):
            command.downgrade(_alembic_config(connection), "base")
    assert schema_status(engine)["status"] == "current"
    with factory() as session:
        assert session.scalars(select(Mod)).all() == []
    engine.dispose()


def test_migration_resources_are_inside_python_package():
    import modsage.migrations

    root = Path(modsage.migrations.__file__).parent
    assert (root / "env.py").is_file()
    assert (root / "versions" / "0001_v05_baseline.py").is_file()
