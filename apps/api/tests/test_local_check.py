"""Deployment diagnostics use isolated loopback fixtures, never real provider services."""

import importlib.util
import json
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "modsage_local_check", ROOT / "scripts/local_check.py"
)
check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check
SPEC.loader.exec_module(check)


def fixture_health():
    return {
        "status": "ok",
        "database": "ok",
        "version": "0.6.0",
        "llm_configured": False,
        "embedding_configured": False,
        "schema": {
            "status": "current",
            "current_revision": "0001_v05_baseline",
            "head_revision": "0001_v05_baseline",
        },
        "providers": {
            name: {"configuration": "not_configured", "calls_enabled": False, "last_call": None}
            for name in ("llm", "embedding")
        },
    }


@contextmanager
def server(handler):
    service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


@pytest.fixture
def local_stack():
    state = {"health": fixture_health(), "total": 0, "requests": [], "token": None}
    job_id = "00000000-0000-0000-0000-000000000006"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, payload, *, kind="application/json", code=200):
            content = json.dumps(payload).encode() if kind == "application/json" else payload
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            state["requests"].append(("GET", self.path))
            if self.path == "/api/v1/health":
                self.send(state["health"])
            elif self.path == "/api/v1/stats":
                self.send({"total_mods": state["total"]})
            elif self.path == "/":
                self.send(b"<html><title>ModSage TEST FIXTURE</title></html>", kind="text/html")
            elif self.path == "/media/warhammer3/official-key-art.jpg":
                self.send(b"\xff\xd8\xffTEST FIXTURE", kind="image/jpeg")
            elif self.path == "/api/v1/recommendation-jobs/" + job_id + "/events":
                if self.headers.get("X-Recommendation-Token") != state["token"]:
                    self.send({}, code=404)
                    return
                cursor = int(self.headers.get("Last-Event-ID", "0"))
                frames = []
                for number, stage in enumerate(
                    ("queued", "understand", "retrieve", "verify", "completed"), 1
                ):
                    if number > cursor:
                        payload = json.dumps({"last_event_id": number, "stage": stage})
                        frames.append(f"id: {number}\nevent: progress\ndata: {payload}\n\n")
                self.send("".join(frames).encode(), kind="text/event-stream")
            else:
                self.send({}, code=404)

        def do_POST(self):
            state["requests"].append(("POST", self.path))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["query"].startswith("本地部署验收：")
            state["token"] = self.headers.get("X-Recommendation-Token")
            self.send(
                {
                    "id": job_id,
                    "status": "completed",
                    "last_event_id": 5,
                    "result": {
                        "run_id": job_id,
                        "status": "completed",
                        "engine": "rules",
                        "retrieval": {"method": "bm25"},
                    },
                },
                code=202,
            )

    with server(Handler) as base:
        yield base, state


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "http://127.0.0.1.evil.example",
        "http://127.1",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:0",
        "http://127.0.0.1:",
        "http://[::1",
        "http://name:SECRET@127.0.0.1:3000",
        "http://127.0.0.1:3000/?key=SECRET",
        "http://127.0.0.1:3000/#SECRET",
        "http://127.0.0.1:3000/api",
        "http://127.0.0.1:3000\n",
        "http://127.0.0.1\\@example.com",
        "file:///tmp/data",
    ],
)
def test_origin_rejects_external_credentials_invalid_ports_and_ambiguous_urls(value):
    with pytest.raises(check.CheckFailure, match="invalid_loopback_origin"):
        check.loopback_origin(value)


def test_localhost_is_pinned_and_ipv6_is_supported():
    assert check.loopback_origin("http://localhost:3000/") == "http://127.0.0.1:3000"
    assert check.loopback_origin("https://[::1]:3443") == "https://[::1]:3443"


def test_default_running_is_read_only_and_empty_catalog_is_not_ready_data(local_stack):
    base, state = local_stack
    result = check.running(base)
    assert result["status"] == "warning"
    assert result["read_only"] is True
    assert result["provider_billing_verified"] is False
    assert {method for method, _ in state["requests"]} == {"GET"}
    assert next(row for row in result["checks"] if row["check"] == "catalog")["details"] == {
        "total_mods": 0
    }


def test_require_data_fails_empty_stack_and_prevents_job_creation(local_stack):
    base, state = local_stack
    result = check.running(base, require_data=True, run_exercise=True)
    assert result["status"] == "failed"
    assert all(method == "GET" for method, _ in state["requests"])


@pytest.mark.parametrize("kind", ["missing", "legacy", "mismatched_revision"])
def test_unmigrated_schema_prevents_diagnostic_job(local_stack, kind):
    base, state = local_stack
    if kind == "missing":
        del state["health"]["schema"]
    elif kind == "legacy":
        state["health"]["schema"]["status"] = "legacy"
    else:
        state["health"]["schema"]["head_revision"] = "later_revision"
    result = check.running(base, run_exercise=True)
    assert result["status"] == "failed"
    assert all(method == "GET" for method, _ in state["requests"])


def test_exercise_checks_retained_job_events_replay_and_access_without_reporting_tokens(
    local_stack,
):
    base, state = local_stack
    state["total"] = 2
    result = check.running(base, require_data=True, run_exercise=True)
    assert result["status"] == "passed"
    evidence = next(row for row in result["checks"] if row["check"] == "recommendation")["details"]
    assert evidence["event_count"] == 5
    assert evidence["job_created"] is True
    assert evidence["provider_billing_verified"] is False
    assert result["worker_configuration_verified"] is False
    assert state["token"] not in json.dumps(result)
    assert sum(method == "POST" for method, _ in state["requests"]) == 2


@pytest.mark.parametrize(
    "mutation", ["enabled", "configured", "incomplete", "missing", "string_false"]
)
def test_exercise_refuses_uncertain_or_enabled_models_before_any_post(local_stack, mutation):
    base, state = local_stack
    health = state["health"]
    if mutation == "enabled":
        health["providers"]["embedding"]["calls_enabled"] = True
    elif mutation == "configured":
        health["llm_configured"] = True
    elif mutation == "incomplete":
        health["providers"]["llm"]["configuration"] = "incomplete"
    elif mutation == "missing":
        del health["providers"]
    else:
        health["providers"]["llm"]["calls_enabled"] = "false"
    result = check.running(base, run_exercise=True)
    assert result["status"] == "blocked"
    assert all(method == "GET" for method, _ in state["requests"])


def test_local_transport_ignores_environment_proxies(local_stack, monkeypatch):
    base, _ = local_stack
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert check.LocalHTTP(base).request("/api/v1/health")[2]["status"] == "ok"


def test_redirect_never_follows_or_reports_secret_location():
    seen = []

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://example.com/private?key=DO_NOT_REPORT")
            self.end_headers()

    with server(Redirect) as base:
        with pytest.raises(check.CheckFailure, match="redirect_refused") as caught:
            check.LocalHTTP(base).request("/health")
    assert seen == ["/health"]
    assert "DO_NOT_REPORT" not in str(caught.value)


def test_response_size_limit_is_enforced_without_echo(monkeypatch):
    monkeypatch.setattr(check, "MAX_BODY", 32)

    class Oversized(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"x" * 1000)

    with server(Oversized) as base:
        with pytest.raises(check.CheckFailure, match="response_too_large"):
            check.LocalHTTP(base).request("/health")


def test_wall_clock_timeout_stops_dripping_http_headers():
    class Drip(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            try:
                for byte in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n":
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.015)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with server(Drip) as base:
        started = time.monotonic()
        with pytest.raises(check.CheckFailure, match="local_request_timeout"):
            check.LocalHTTP(base).request("/health", timeout=0.1)
        assert time.monotonic() - started < 1


def create_project(root):
    for name in (
        "compose.yaml",
        ".env.example",
        "apps/api/pyproject.toml",
        "apps/web/package.json",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("TEST FIXTURE", encoding="utf-8")


def test_docker_route_needs_no_native_dependencies_and_preserves_existing_config(
    tmp_path, monkeypatch
):
    create_project(tmp_path)
    secret = "MODSAGE_LLM_API_KEY=PRIVATE_TEST_VALUE\n"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")
    calls = []
    ports = []

    def fake_port(port):
        ports.append(port)
        return check.item(str(port), "passed", "free")

    def fake_command(args, **_):
        calls.append(args)
        if args[1:3] == ["compose", "version"]:
            return 0, "2.39.0\n"
        if args[1:3] == ["context", "inspect"]:
            return 0, "unix:///var/run/docker.sock\n"
        if args[1] == "info":
            return 0, "28.0.1\n"
        raise AssertionError("Docker mode must not probe native runtime")

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(check.shutil, "which", lambda _: "/test/docker")
    monkeypatch.setattr(check, "command", fake_command)
    monkeypatch.setattr(check, "port_check", fake_port)
    result = check.doctor("docker", tmp_path)
    assert result["status"] == "passed"
    assert (tmp_path / ".env").read_text() == secret
    assert "PRIVATE_TEST_VALUE" not in json.dumps(result)
    assert all(args[0] == "docker" for args in calls)
    assert set(ports) == {3000, 8000, 5432}


@pytest.mark.parametrize("context_overrides_host", [False, True])
def test_docker_remote_context_is_not_contacted(monkeypatch, context_overrides_host):
    if context_overrides_host:
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
        monkeypatch.setenv("DOCKER_CONTEXT", "remote-fixture")
    else:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(check.shutil, "which", lambda _: "/test/docker")
    calls = []

    def fake_command(args, **_):
        calls.append(args)
        return (0, "2.39.0") if args[1] == "compose" else (0, "tcp://private-host.example:2376")

    monkeypatch.setattr(check, "command", fake_command)
    result = check.docker_checks()
    assert result[-1]["status"] == "blocked"
    assert not any(args[1] == "info" for args in calls)
    assert "private-host" not in json.dumps(result)


def test_native_reports_permission_and_missing_dependencies_without_installing(
    tmp_path, monkeypatch
):
    create_project(tmp_path)
    ports = []

    def fake_port(port):
        ports.append(port)
        return check.item(str(port), "passed", "free")

    monkeypatch.setattr(check.os, "access", lambda *_: False)
    monkeypatch.setattr(
        check, "command", lambda args, **_: (0, "v10.0.0") if args[0] == "npm" else (0, "3.11.9")
    )
    monkeypatch.setattr(check, "port_check", fake_port)
    result = check.doctor("native", tmp_path)
    assert result["status"] == "blocked"
    statuses = {row["check"]: row["status"] for row in result["checks"]}
    assert statuses["project_directory"] == "blocked"
    assert statuses["python"] == "blocked"
    assert statuses["python_environment"] == "blocked"
    assert statuses["frontend_dependencies"] == "blocked"
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".venv").exists()
    assert set(ports) == {3000, 8000}


def test_cli_safe_error_and_nonzero_exit_without_echoing_input(capsys):
    result = check.main(["running", "--base-url", "http://PRIVATEKEY@127.0.0.1:3000"])
    output = capsys.readouterr().out
    assert result == 1
    assert "PRIVATEKEY" not in output
    assert json.loads(output)["status"] == "blocked"
