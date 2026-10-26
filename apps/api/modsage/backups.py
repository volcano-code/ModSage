"""Verified, non-overwriting SQLite snapshots for local deployment operations.

This module never initializes or upgrades its source database. A restore is another
verified SQLite backup into a NEW file; selecting it as the application's database
and reconciling restored queue/usage state remain explicit operator actions.
"""

import hashlib
import math
import os
import sqlite3
import stat
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool


class BackupError(Exception):
    """A bounded, safe diagnostic which contains no input values or database rows."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _path(value: str | Path, *, expand_user: bool = True) -> Path:
    try:
        if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
            raise ValueError
        path = Path(value).expanduser() if expand_user else Path(value)
        if not path.is_absolute():
            path = Path.cwd() / path
        # Refuse symlinks in existing parents as well as the final component.
        for component in (*reversed(path.parents), path):
            try:
                metadata = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise BackupError("symlink_path", "Snapshot paths must not contain symlinks.")
        return Path(os.path.abspath(path))
    except BackupError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError):
        raise BackupError(
            "invalid_path", "Provide an accessible ordinary filesystem path."
        ) from None


def _database_path(database: Engine | str) -> Path:
    try:
        url = database.url if isinstance(database, Engine) else make_url(database)
        if url.get_backend_name() != "sqlite":
            raise BackupError(
                "unsupported_database",
                "This command supports SQLite files; use pg_dump/pg_restore for PostgreSQL.",
            )
        if not url.database or url.database == ":memory:" or url.query:
            raise BackupError(
                "file_database_required",
                "A plain file-backed SQLite database URL without URI options is required.",
            )
        # SQLAlchemy treats a database URL path literally; it does not expand '~'.
        return _path(url.database, expand_user=False)
    except BackupError:
        raise
    except Exception:
        raise BackupError("invalid_database_url", "The database URL could not be read.") from None


def _check_paths(source: Path, destination: Path) -> None:
    if source == destination:
        raise BackupError("same_path", "Source and destination must be different files.")
    try:
        source_info = source.lstat()
    except FileNotFoundError:
        raise BackupError("source_missing", "The source database does not exist.") from None
    if not stat.S_ISREG(source_info.st_mode):
        raise BackupError("source_not_file", "The source must be an ordinary database file.")
    for suffix in ("-wal", "-shm", "-journal"):
        companion = Path(str(source) + suffix)
        if companion.is_symlink():
            raise BackupError("symlink_path", "Database companion files must not be symlinks.")
    if os.path.lexists(destination):
        raise BackupError(
            "destination_exists", "The destination already exists; it will not be overwritten."
        )
    if any(os.path.lexists(str(destination) + suffix) for suffix in ("-wal", "-shm", "-journal")):
        raise BackupError(
            "destination_companions_exist",
            "Destination journal files already exist; choose a completely new database path.",
        )
    if not destination.parent.is_dir():
        raise BackupError("destination_parent_missing", "Create the destination directory first.")


def _readonly_connection(path: Path, timeout: float = 1) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _verify_snapshot(path: Path, deadline: float) -> tuple[dict, dict[str, int]]:
    from modsage.database import schema_status

    def progress():
        return int(time.monotonic() >= deadline)

    def connect():
        connection = _readonly_connection(path)
        connection.set_progress_handler(progress, 1000)
        return connection

    # Schema inspection also uses read-only connections, never init_database().
    engine = create_engine("sqlite://", creator=connect, poolclass=NullPool)
    try:
        schema = schema_status(engine)
    finally:
        engine.dispose()
    if time.monotonic() >= deadline:
        raise BackupError("snapshot_timeout", "Snapshot verification exceeded its time budget.")
    if schema["status"] not in {"current", "legacy"}:
        raise BackupError(
            "schema_not_supported",
            "The source schema is not a verified current or supported legacy ModSage schema.",
        )
    with closing(connect()) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("integrity_check_failed", "SQLite integrity validation failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("foreign_key_check_failed", "SQLite foreign-key validation failed.")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        counts = {}
        for (name,) in tables:
            # Names come from the previously verified schema, not from user SQL.
            quoted = '"' + name.replace('"', '""') + '"'
            counts[name] = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
    return schema, counts


def _copy_verified(
    source: Path, destination: Path, *, operation: str, timeout_seconds: float
) -> dict:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 600
    ):
        raise BackupError(
            "invalid_timeout", "The time budget must be positive and at most 600 seconds."
        )
    deadline = time.monotonic() + timeout_seconds
    temporary = None
    published = False

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise BackupError("snapshot_timeout", "The SQLite snapshot exceeded its time budget.")

    try:
        _check_paths(source, destination)
        descriptor, name = tempfile.mkstemp(
            prefix=".modsage-snapshot-", suffix=".db", dir=destination.parent
        )
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with closing(_readonly_connection(source, min(timeout_seconds, 1))) as original:
            with closing(sqlite3.connect(temporary, timeout=min(timeout_seconds, 1))) as target:
                target.execute("PRAGMA synchronous=FULL")
                original.backup(target, pages=256, progress=progress, sleep=0.01)
                # A completed deliverable is self-contained, including a WAL source.
                if target.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise BackupError(
                        "snapshot_not_standalone", "The snapshot could not be finalized."
                    )
        schema, counts = _verify_snapshot(temporary, deadline)
        checksum = hashlib.sha256()
        with temporary.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                progress(None, None, None)
                checksum.update(chunk)
            os.fsync(stream.fileno())
        size = temporary.stat().st_size
        # Recheck paths after copying. Atomic link creation is the final no-replace
        # guard even if another operation creates the destination after this check.
        _path(destination)
        _check_paths(source, destination)
        os.link(temporary, destination, follow_symlinks=False)
        published = True
        temporary.unlink()
        temporary = None
        directory = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        warnings = []
        if schema["status"] == "legacy":
            warnings.append("legacy_schema_preserved_explicit_upgrade_required")
        if operation == "restore":
            warnings.extend(
                [
                    "new_database_only_application_configuration_unchanged",
                    "keep_provider_calls_disabled_until_tasks_and_usage_are_reconciled",
                    "restored_usage_is_a_snapshot_not_the_provider_billing_balance",
                ]
            )
        return {
            "status": "succeeded",
            "operation": operation,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_open_mode": "read_only",
            "schema": schema,
            "integrity_check": "ok",
            "foreign_key_check": "ok",
            "table_counts": counts,
            "bytes": size,
            "sha256": checksum.hexdigest(),
            "network_calls": 0,
            "warnings": warnings,
        }
    except BackupError:
        raise
    except FileExistsError:
        raise BackupError(
            "destination_exists", "The destination already exists; it was not overwritten."
        ) from None
    except sqlite3.Error:
        if time.monotonic() >= deadline:
            raise BackupError(
                "snapshot_timeout", "The SQLite snapshot exceeded its time budget."
            ) from None
        raise BackupError(
            "invalid_database", "SQLite could not copy or validate the source database."
        ) from None
    except Exception:
        if published:
            raise BackupError(
                "publication_durability_unknown",
                "A verified destination exists, but directory durability could not be confirmed; do not overwrite it.",
            ) from None
        raise BackupError(
            "snapshot_failed", "The snapshot could not be safely completed."
        ) from None
    finally:
        if temporary is not None:
            # Only paths derived from our exclusive random temporary file are removed.
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    Path(str(temporary) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass


def backup_sqlite(
    database: Engine | str, destination: str | Path, *, timeout_seconds: float = 60
) -> dict:
    """Back up an existing file DB; refuse an existing target and never migrate."""
    return _copy_verified(
        _database_path(database),
        _path(destination),
        operation="backup",
        timeout_seconds=timeout_seconds,
    )


def restore_sqlite(
    source: str | Path, destination: str | Path, *, timeout_seconds: float = 60
) -> dict:
    """Verify and restore a snapshot to a new file, without changing app settings."""
    return _copy_verified(
        _path(source), _path(destination), operation="restore", timeout_seconds=timeout_seconds
    )
