"""Exercise the actual offline maintenance entry points without provider credentials."""

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import text

from modsage.main import create_app


def invoke(tmp_path, command, *args, extra_env=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("MODSAGE_")}
    env.update(
        MODSAGE_DATABASE_URL=f"sqlite:///{tmp_path / 'catalog.db'}",
        MODSAGE_PROVIDER_CALLS_ENABLED="false",
        MODSAGE_LLM_API_KEY="",
        MODSAGE_EMBEDDING_API_KEY="",
        MODSAGE_STEAM_API_KEY="",
    )
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, "-m", "modsage.cli", command, *map(str, args)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    body = json.loads(result.stdout if result.stdout.strip() else result.stderr)
    return result, body


def test_status_does_not_create_database_or_parent_directories(tmp_path):
    target = tmp_path / "absent-parent" / "absent.db"
    result, body = invoke(
        tmp_path, "db-status", extra_env={"MODSAGE_DATABASE_URL": f"sqlite:///{target}"}
    )
    assert result.returncode == 1
    assert body["status"] == "empty"
    assert not target.parent.exists()


def test_cli_initializes_revision_and_repeated_upgrade_is_idempotent(tmp_path):
    result, body = invoke(tmp_path, "db-upgrade")
    assert result.returncode == 0
    assert body["status"] == "ready"
    assert body["application_version"] == "0.6.0"
    assert body["schema"]["status"] == "current"
    assert body["migration_action"] == "created"
    repeated, repeated_body = invoke(tmp_path, "init-db")
    assert repeated.returncode == 0
    assert repeated_body["migration_action"] == "unchanged"
    inspected, inspected_body = invoke(tmp_path, "db-status")
    assert inspected.returncode == 0
    assert inspected_body["current_revision"] == body["schema"]["head_revision"]


def test_cli_backup_restore_and_refused_overwrite(tmp_path):
    result, _ = invoke(tmp_path, "init-db")
    assert result.returncode == 0
    backup = tmp_path / "snapshot.db"
    result, body = invoke(tmp_path, "backup-db", "--output", backup)
    assert result.returncode == 0
    assert body["status"] == "succeeded"
    assert body["integrity_check"] == "ok"
    original_bytes = backup.read_bytes()
    result, body = invoke(tmp_path, "backup-db", "--output", backup)
    assert result.returncode == 1
    assert body["error_code"] == "destination_exists"
    assert backup.read_bytes() == original_bytes
    restored = tmp_path / "restored.db"
    # Offline restore remains possible even if unrelated provider settings are broken.
    result, body = invoke(
        tmp_path,
        "restore-db",
        "--input",
        backup,
        "--output",
        restored,
        extra_env={"MODSAGE_LLM_TIMEOUT_SECONDS": "invalid-private-setting"},
    )
    assert result.returncode == 0
    assert body["schema"]["status"] == "current"
    assert restored.is_file()
    assert "invalid-private-setting" not in result.stdout + result.stderr
    assert str(tmp_path) not in result.stdout + result.stderr


def test_backup_command_does_not_initialize_a_missing_source(tmp_path):
    result, body = invoke(tmp_path, "backup-db", "--output", tmp_path / "snapshot.db")
    assert result.returncode == 1
    assert body["error_code"] == "source_missing"
    assert not (tmp_path / "catalog.db").exists()
    assert not (tmp_path / "snapshot.db").exists()


def test_backup_rejects_postgresql_without_connecting_or_echoing_credentials(tmp_path):
    result, body = invoke(
        tmp_path,
        "backup-db",
        "--output",
        tmp_path / "snapshot.db",
        extra_env={
            "MODSAGE_DATABASE_URL": "postgresql+psycopg://user:private-marker@invalid.invalid/db"
        },
    )
    assert result.returncode == 1
    assert body["error_code"] == "unsupported_database"
    assert "private-marker" not in result.stdout + result.stderr
    assert "invalid.invalid" not in result.stdout + result.stderr
    assert not (tmp_path / "snapshot.db").exists()


def test_health_checks_recorded_revision_without_echoing_an_unknown_revision(settings):
    with TestClient(create_app(settings)) as client:
        healthy = client.get("/api/v1/health")
        assert healthy.status_code == 200
        assert healthy.json()["schema"]["status"] == "current"
        with client.app.state.engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": "private-unknown-revision"},
            )
        unhealthy = client.get("/api/v1/health")
        assert unhealthy.status_code == 503
        assert "private-unknown-revision" not in unhealthy.text
        assert unhealthy.headers["Cache-Control"] == "no-store"


def test_health_handles_missing_version_table_without_raw_sql(settings):
    with TestClient(create_app(settings)) as client:
        with client.app.state.engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
        response = client.get("/api/v1/health")
        assert response.status_code == 503
        assert "SELECT" not in response.text
        assert "alembic_version" not in response.text
