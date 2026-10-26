"""Guarded Alembic upgrades and read-only schema inspection.

The initial revision exactly matches v0.5. An unversioned database is adopted only
after comparing its tables, columns, keys, indexes and defaults to that frozen
baseline. Unknown revisions, partial schemas and drift are never auto-repaired.
"""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import String, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from modsage.migrations.baseline_v05 import baseline_metadata

HEAD_REVISION = "0001_v05_baseline"
LOCK_TIMEOUT_SECONDS = 10
_ADVISORY_LOCK_ID = 0x4D4F4453414745
# Alembic's command environment uses module-level proxy objects. Database locks
# serialize separate processes; this lock also serializes threads in one process.
_COMMAND_LOCK = Lock()


class SchemaMigrationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _report(dialect: str, status: str, revision=None, issues=()) -> dict:
    return {
        "status": status,
        "dialect": dialect,
        "current_revision": revision,
        "head_revision": HEAD_REVISION,
        "issues": sorted(set(issues)),
    }


def _index_issues(connection, inspector, name, table) -> list[str]:
    """Supplement Alembic's comparison for index predicates/order/collation.

    SQLite reflection omits expression indexes and Alembic does not compare a
    partial-index predicate. The frozen baseline has only ordinary B-tree
    indexes with ascending default collation, so other definitions are drift.
    """
    issues = []
    expected = {index.name: list(index.columns.keys()) for index in table.indexes}
    if connection.dialect.name == "sqlite":
        quote = connection.dialect.identifier_preparer.quote_identifier
        indexes = connection.exec_driver_sql(f"PRAGMA index_list({quote(name)})").mappings().all()
        explicit = {index["name"] for index in indexes if index["origin"] == "c"}
        if explicit - expected.keys():
            issues.append("unexpected_indexes")
        if expected.keys() - explicit:
            issues.append("missing_indexes")
        for index in indexes:
            keys = [
                row
                for row in connection.exec_driver_sql(
                    f"PRAGMA index_xinfo({quote(index['name'])})"
                ).mappings()
                if row["key"]
            ]
            if (
                index["partial"]
                or any(key["cid"] < 0 or key["desc"] or key["coll"] != "BINARY" for key in keys)
                or (
                    index["origin"] == "c"
                    and [key["name"] for key in keys] != expected.get(index["name"])
                )
            ):
                issues.append("index_definition_mismatch")
    else:
        for index in inspector.get_indexes(name):
            if index.get("column_sorting") or any(
                column is None for column in index.get("column_names", [])
            ):
                issues.append("index_definition_mismatch")
            for option, value in index.get("dialect_options", {}).items():
                if value and not (option == "postgresql_using" and value == "btree"):
                    issues.append("index_definition_mismatch")
    return issues


def _schema_issues(connection, tables: set[str]) -> list[str]:
    expected = baseline_metadata()
    inspector = inspect(connection)
    expected_tables = set(expected.tables)
    issues = []
    if expected_tables - tables:
        issues.append("missing_tables")
    if tables - expected_tables:
        issues.append("unexpected_tables")
    if issues:
        return issues
    if inspector.get_view_names():
        issues.append("unexpected_views")
    if connection.dialect.name == "postgresql" and inspector.get_materialized_view_names():
        issues.append("unexpected_materialized_views")
    for name, table in expected.tables.items():
        actual = inspector.get_pk_constraint(name).get("constrained_columns", [])
        if list(table.primary_key.columns.keys()) != list(actual):
            issues.append("primary_key_mismatch")
        # Alembic autogeneration does not compare arbitrary CHECK constraints.
        if inspector.get_check_constraints(name):
            issues.append("unexpected_check_constraints")
        for column in inspector.get_columns(name):
            expected_column = table.columns.get(column["name"])
            if expected_column is None:
                continue
            if column.get("computed") or column.get("identity"):
                issues.append("unexpected_generated_column")
            # Alembic deliberately skips type arguments when either side omits
            # them; an unbounded VARCHAR must not be adopted as VARCHAR(128).
            if isinstance(expected_column.type, String) and getattr(
                column["type"], "length", None
            ) != getattr(expected_column.type, "length", None):
                issues.append("column_type_mismatch")
            if (
                connection.dialect.name == "postgresql"
                and expected_column is table._autoincrement_column
            ):
                # Alembic skips SERIAL defaults; losing/repointing one would pass
                # normal comparison but fail or corrupt the next source insert.
                default = column.get("default")
                valid_default = (
                    column.get("autoincrement")
                    and isinstance(default, str)
                    and re.fullmatch(r"nextval\('(?:[^']|'')+'::regclass\)", default)
                )
                if valid_default:
                    valid_default = connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_attrdef a "
                            "JOIN pg_attribute att ON att.attrelid=a.adrelid "
                            "AND att.attnum=a.adnum "
                            "JOIN pg_depend d ON d.objid=a.oid "
                            "AND d.classid='pg_attrdef'::regclass "
                            "AND d.refclassid='pg_class'::regclass "
                            "WHERE a.adrelid=to_regclass(:table_name) "
                            "AND att.attname=:column_name "
                            "AND d.refobjid=to_regclass("
                            "pg_get_serial_sequence(:table_name, :column_name)))"
                        ),
                        {"table_name": name, "column_name": column["name"]},
                    ).scalar_one()
                if not valid_default:
                    issues.append("autoincrement_default_mismatch")
        issues.extend(_index_issues(connection, inspector, name, table))
    if connection.dialect.name == "sqlite":
        if connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1"
        ).first():
            issues.append("unexpected_triggers")
    else:
        if connection.execute(
            text(
                "SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND NOT t.tgisinternal LIMIT 1"
            )
        ).first():
            issues.append("unexpected_triggers")
    if issues:
        return issues
    options = {
        "compare_type": True,
        "compare_server_default": True,
        "include_object": lambda obj, name, type_, reflected, compare_to: (
            not (type_ == "table" and name == "alembic_version")
        ),
    }
    differences = compare_metadata(MigrationContext.configure(connection, opts=options), expected)
    categories = {
        "add_column": "missing_columns",
        "remove_column": "unexpected_columns",
        "modify_type": "column_type_mismatch",
        "modify_nullable": "column_nullability_mismatch",
        "modify_default": "column_default_mismatch",
        "add_index": "missing_indexes",
        "remove_index": "unexpected_indexes",
        "add_constraint": "missing_constraints",
        "remove_constraint": "unexpected_constraints",
        "add_fk": "missing_foreign_keys",
        "remove_fk": "unexpected_foreign_keys",
    }
    for group in differences:
        for change in group if isinstance(group, list) else [group]:
            issues.append(categories.get(change[0], "schema_drift"))
    return issues


def _inspect_schema(connection) -> dict:
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        return _report(dialect, "blocked", issues=["unsupported_database"])
    tables = set(inspect(connection).get_table_names())
    versioned = "alembic_version" in tables
    tables.discard("alembic_version")
    if not tables and not versioned:
        if inspect(connection).get_view_names():
            return _report(dialect, "blocked", issues=["unexpected_views"])
        if dialect == "postgresql" and inspect(connection).get_materialized_view_names():
            return _report(dialect, "blocked", issues=["unexpected_materialized_views"])
        return _report(dialect, "empty")
    revision = None
    if versioned:
        version_inspector = inspect(connection)
        columns = version_inspector.get_columns("alembic_version")
        primary = version_inspector.get_pk_constraint("alembic_version")
        if (
            len(columns) != 1
            or columns[0]["name"] != "version_num"
            or columns[0]["nullable"]
            or getattr(columns[0]["type"], "length", None) != 32
            or columns[0].get("default") is not None
            or primary.get("constrained_columns") != ["version_num"]
        ):
            return _report(dialect, "blocked", issues=["invalid_revision_table"])
        try:
            revisions = MigrationContext.configure(connection).get_current_heads()
        except DBAPIError:
            return _report(dialect, "blocked", issues=["invalid_revision_table"])
        if len(revisions) != 1 or revisions[0] != HEAD_REVISION:
            # Do not print arbitrary contents from an unknown version table.
            return _report(dialect, "blocked", issues=["unknown_revision"])
        revision = revisions[0]
    issues = _schema_issues(connection, tables)
    return _report(
        dialect,
        "blocked" if issues else ("current" if revision else "legacy"),
        revision,
        issues,
    )


def schema_status(engine) -> dict:
    """Inspect without DDL, stamps or creation of a missing SQLite file.

    File-backed SQLite is opened in mode=ro on a separate connection, including
    when the caller's engine was configured for normal read/write operation.
    """
    if engine.dialect.name == "sqlite":
        database = engine.url.database
        if "uri" in engine.url.query:
            return _report("sqlite", "blocked", issues=["unsupported_sqlite_uri"])
        if database and database != ":memory:":
            # Match SQLAlchemy SQLite URL semantics, including a literal '~'.
            path = Path(database).resolve()
            if not path.exists():
                return _report("sqlite", "empty")
            if not path.is_file():
                return _report("sqlite", "blocked", issues=["invalid_database_path"])
            readonly_engine = create_engine(
                "sqlite://",
                creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10),
                poolclass=NullPool,
            )
            try:
                with readonly_engine.connect() as connection:
                    return _inspect_schema(connection)
            finally:
                readonly_engine.dispose()
    with engine.connect() as connection:
        return _inspect_schema(connection)


def _alembic_config(connection) -> Config:
    config = Config()
    location = str(Path(__file__).parent / "migrations")
    config.set_main_option("script_location", location.replace("%", "%%"))
    config.attributes["connection"] = connection
    return config


@contextmanager
def _locked_connection(engine):
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise SchemaMigrationError("unsupported_database")
    if engine.dialect.name == "sqlite" and "uri" in engine.url.query:
        raise SchemaMigrationError("unsupported_sqlite_uri")
    if not _COMMAND_LOCK.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise SchemaMigrationError("migration_lock_timeout")
    try:
        with engine.connect() as connection:
            previous_timeout = None
            try:
                if connection.dialect.name == "sqlite":
                    previous_timeout = connection.exec_driver_sql(
                        "PRAGMA busy_timeout"
                    ).scalar_one()
                    connection.exec_driver_sql(
                        f"PRAGMA busy_timeout={int(LOCK_TIMEOUT_SECONDS * 1000)}"
                    )
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    connection.execute(
                        text("SELECT set_config('lock_timeout', :timeout, true)"),
                        {"timeout": f"{int(LOCK_TIMEOUT_SECONDS * 1000)}ms"},
                    )
                    connection.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_id)"),
                        {"lock_id": _ADVISORY_LOCK_ID},
                    )
                yield connection
                connection.commit()
            except DBAPIError as exc:
                connection.rollback()
                code = getattr(exc.orig, "sqlstate", None)
                if code == "55P03" or getattr(exc.orig, "sqlite_errorcode", None) in {5, 6}:
                    raise SchemaMigrationError("migration_lock_timeout") from None
                raise SchemaMigrationError("migration_database_error") from None
            except BaseException:
                connection.rollback()
                raise
            finally:
                if connection.dialect.name == "sqlite" and previous_timeout is not None:
                    connection.exec_driver_sql(f"PRAGMA busy_timeout={previous_timeout}")
    finally:
        _COMMAND_LOCK.release()


def upgrade_database(engine) -> dict:
    """Create an empty database or safely adopt v0.5, under one database lock.

    This release has a single frozen baseline. Future changes must add new
    immutable Alembic revisions and validation metadata before upgrading them.
    """
    with _locked_connection(engine) as connection:
        before = _inspect_schema(connection)
        if before["status"] == "blocked":
            raise SchemaMigrationError(before["issues"][0])
        config = _alembic_config(connection)
        action = "unchanged"
        if before["status"] == "legacy":
            command.stamp(config, HEAD_REVISION)
            action = "adopted_legacy"
        elif before["status"] == "empty":
            command.upgrade(config, "head")
            action = "created"
        after = _inspect_schema(connection)
        if after["status"] != "current":
            raise SchemaMigrationError("migration_verification_failed")
        return {**after, "action": action}
