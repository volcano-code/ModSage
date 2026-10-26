"""Check a local ModSage installation without packages, credentials or source discovery.

Doctor checks are read-only. Running checks only GET by default; --exercise explicitly
creates one diagnostic recommendation job, retained by the normal application policy.
HTTP transport uses numeric loopback, ignores proxy environment, and never redirects.
"""

import argparse
import errno
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 2 * 1024 * 1024


class CheckFailure(Exception):
    """A safe, fixed diagnostic code, never an upstream response or exception text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def item(name, status, message, **details):
    result = {"check": name, "status": status, "message": message}
    if details:
        result["details"] = details
    return result


def report(mode, checks, **details):
    states = {check["status"] for check in checks}
    status = next(
        (state for state in ("failed", "blocked", "warning") if state in states), "passed"
    )
    return {"status": status, "mode": mode, "checks": checks, **details}


def command(args, *, timeout=10):
    """Only execute argument lists; callers never expose raw stdout or stderr."""
    try:
        process = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return process.returncode, process.stdout[:4096]
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def version_tuple(value):
    match = re.fullmatch(r"\s*(?:Python |v)?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][\w.-]+)?\s*", value)
    return tuple(int(number or 0) for number in match.groups()) if match else None


def version_check(name, args, minimum):
    code, output = command(args)
    parsed = version_tuple(output) if code == 0 else None
    if parsed is None:
        return item(name, "blocked", "命令不可用或版本无法识别；请安装该运行时后重试。")
    if parsed < minimum:
        return item(name, "blocked", "运行时版本过低；请按本地部署文档升级。", version=list(parsed))
    return item(name, "passed", "运行时版本满足要求。", version=list(parsed))


def path_access(path, name):
    try:
        stat = path.stat()
        readable = os.access(path, os.R_OK | os.X_OK)
        writable = os.access(path, os.W_OK | os.X_OK)
        owned = stat.st_uid == os.geteuid() if hasattr(os, "geteuid") else None
        if not path.is_dir() or not readable or not writable:
            return item(
                name,
                "blocked",
                "当前用户不能读写此目录；检查目录所有权与权限，不要用 sudo 安装项目依赖。",
                owned_by_current_user=owned,
                readable=readable,
                writable=writable,
            )
        return item(name, "passed", "当前用户能够读写目录。", owned_by_current_user=owned)
    except OSError:
        return item(name, "blocked", "目录不存在或不可访问；核对仓库位置及父目录权限。")


def port_check(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            error = probe.connect_ex(("127.0.0.1", port))
    except OSError:
        return item(f"port_{port}", "warning", "当前环境不允许检查本地端口；启动时还需检查。")
    if error == 0:
        return item(
            f"port_{port}",
            "blocked",
            "本地端口已被占用；已有 ModSage 可改用 running 检查，否则先确认占用进程。",
        )
    if error not in {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH}:
        return item(f"port_{port}", "warning", "不能确定本地端口是否空闲；启动时还需检查。")
    return item(f"port_{port}", "passed", "未观察到本地端口上的监听服务；启动时仍可能发生竞争。")


def docker_checks():
    if not shutil.which("docker"):
        return [item("docker", "blocked", "未找到 Docker；按文档安装 Engine 与 Compose 插件。")]
    checks = [version_check("compose", ["docker", "compose", "version", "--short"], (2, 0, 0))]
    # Docker's explicit context takes precedence over DOCKER_HOST.
    host = "" if os.environ.get("DOCKER_CONTEXT") else os.environ.get("DOCKER_HOST", "")
    if host and not host.startswith("unix://"):
        checks.append(item("docker_context", "blocked", "本地检查不连接远程 Docker daemon。"))
        return checks
    if not host:
        code, host = command(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
        )
        if code != 0:
            checks.append(item("docker_context", "blocked", "无法读取当前 Docker context。"))
            return checks
    if not host.strip().startswith("unix://"):
        checks.append(
            item("docker_context", "blocked", "本地检查只允许 Unix socket Docker context。")
        )
        return checks
    code, output = command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
    if code != 0 or version_tuple(output) is None:
        checks.append(
            item(
                "docker_daemon", "blocked", "无法连接本地 Docker；检查服务是否启动及当前用户权限。"
            )
        )
    else:
        checks.append(item("docker_daemon", "passed", "当前用户可以连接本地 Docker daemon。"))
    return checks


def doctor(mode, root=ROOT):
    root = Path(root)
    checks = [path_access(root, "project_directory")]
    required = ("compose.yaml", ".env.example", "apps/api/pyproject.toml", "apps/web/package.json")
    if not all((root / name).is_file() for name in required):
        checks.append(item("project_files", "blocked", "未找到完整仓库文件；请从仓库根目录运行。"))
        return report("doctor", checks, deployment=mode, modified_files=False)
    checks.append(item("project_files", "passed", "找到项目入口与环境变量示例。"))
    configuration = root / ".env"
    if configuration.exists():
        if not configuration.is_file() or not os.access(configuration, os.R_OK):
            checks.append(item("configuration_file", "blocked", "已有 .env 不可读取；请修正权限。"))
        else:
            checks.append(
                item(
                    "configuration_file",
                    "passed",
                    "已有 .env；保留原文件，内容另由 preflight 校验。",
                )
            )
    else:
        checks.append(
            item(
                "configuration_file",
                "warning",
                "尚无 .env；按部署文档从示例创建，不需要填写 API Key。",
            )
        )
    if mode == "docker":
        checks.extend(docker_checks())
    else:
        interpreter = root / ".venv/bin/python"
        checks.append(version_check("python", [sys.executable, "--version"], (3, 12, 0)))
        checks.append(version_check("node", ["node", "--version"], (22, 0, 0)))
        checks.append(version_check("npm", ["npm", "--version"], (10, 0, 0)))
        if not interpreter.is_file():
            checks.append(
                item(
                    "python_environment", "blocked", "尚无 .venv；按文档创建并安装锁定的后端依赖。"
                )
            )
        else:
            checks.append(version_check("venv_python", [str(interpreter), "--version"], (3, 12, 0)))
            code, _ = command(
                [
                    str(interpreter),
                    "-c",
                    "import fastapi, sqlalchemy, modsage, uvicorn; import alembic",
                ],
            )
            checks.append(
                item(
                    "backend_dependencies",
                    "passed" if code == 0 else "blocked",
                    "后端依赖可导入。"
                    if code == 0
                    else "后端依赖缺失；按文档安装锁定依赖及本项目。",
                )
            )
        if not (root / "apps/web/node_modules/next/package.json").is_file():
            checks.append(
                item(
                    "frontend_dependencies",
                    "blocked",
                    "前端依赖尚未安装；执行 npm --prefix apps/web ci。",
                )
            )
        else:
            checks.append(
                item(
                    "frontend_dependencies", "passed", "找到已安装的 Next.js；构建由后续步骤验收。"
                )
            )
        for name in (".venv", "apps/web", "apps/web/node_modules", "apps/web/.next", "data"):
            if (root / name).exists():
                checks.append(path_access(root / name, "permissions_" + name.replace("/", "_")))
    ports = (3000, 8000, 5432) if mode == "docker" else (3000, 8000)
    checks.extend(port_check(port) for port in ports)
    return report("doctor", checks, deployment=mode, modified_files=False)


def loopback_origin(value):
    if not isinstance(value, str) or any(
        ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value
    ):
        raise CheckFailure("invalid_loopback_origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.netloc.endswith(":")
            or port == 0
        ):
            raise CheckFailure("invalid_loopback_origin")
    except ValueError:
        raise CheckFailure("invalid_loopback_origin") from None
    # Pin localhost to a literal loopback IP; do not trust DNS or proxy environment.
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")


class LocalHTTP:
    def __init__(self, base):
        self.base = loopback_origin(base)
        self.requests = 0

    def request(self, path, *, body=None, headers=None, expect_json=True, timeout=10):
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise CheckFailure("invalid_local_path")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {"Accept": "application/json" if expect_json else "*/*"}
        request_headers.update(headers or {})
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        parsed = urlsplit(self.base)
        connection_type = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
        transports = []
        expired = threading.Event()

        def stop_request():
            expired.set()
            for transport in transports:
                if transport is None:
                    continue
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

        # A wall-clock deadline also bounds slow/dripping headers and body reads;
        # socket timeouts alone would be reset by each new byte.
        deadline = threading.Timer(timeout, stop_request)
        deadline.daemon = True
        deadline.start()
        self.requests += 1
        try:
            connection.connect()
            transports.append(connection.sock)
            if expired.is_set():
                raise CheckFailure("local_request_timeout")
            connection.request(
                "GET" if data is None else "POST", path, body=data, headers=request_headers
            )
            with connection.getresponse() as response:
                if 300 <= response.status < 400:
                    raise CheckFailure("redirect_refused")
                if response.status >= 400:
                    raise CheckFailure("http_" + str(response.status))
                content = response.read(MAX_BODY + 1)
                if expired.is_set():
                    raise CheckFailure("local_request_timeout")
                if len(content) > MAX_BODY:
                    raise CheckFailure("response_too_large")
                if response.status != 200 and not (data is not None and response.status == 202):
                    raise CheckFailure("unexpected_http_status")
                if expect_json:
                    if response.headers.get_content_type() != "application/json":
                        raise CheckFailure("invalid_json_response")
                    try:
                        content = json.loads(content)
                    except (ValueError, UnicodeError):
                        raise CheckFailure("invalid_json_response") from None
                    if not isinstance(content, dict):
                        raise CheckFailure("invalid_json_response")
                return response.status, response.headers, content
        except (OSError, http.client.HTTPException):
            raise CheckFailure(
                "local_request_timeout" if expired.is_set() else "local_service_unreachable"
            ) from None
        finally:
            deadline.cancel()
            connection.close()


def models_disabled(health):
    if health.get("llm_configured") is not False or health.get("embedding_configured") is not False:
        return False
    providers = health.get("providers")
    if not isinstance(providers, dict):
        return False
    return all(
        isinstance(providers.get(name), dict)
        and providers[name].get("configuration") == "not_configured"
        and providers[name].get("calls_enabled") is False
        for name in ("llm", "embedding")
    )


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def progress_events(body):
    try:
        text = body.decode("utf-8").replace("\r\n", "\n")
        events = []
        for block in text.split("\n\n"):
            fields = dict(
                (key, value.lstrip(" "))
                for key, separator, value in (line.partition(":") for line in block.splitlines())
                if separator and key
            )
            if "data" not in fields:
                continue
            value = json.loads(fields["data"])
            event_id = int(fields["id"])
            require(fields.get("event") == "progress", "invalid_sse")
            require(
                isinstance(value, dict) and value.get("last_event_id") == event_id, "invalid_sse"
            )
            events.append((event_id, value))
        return events
    except (ValueError, KeyError, UnicodeError):
        raise CheckFailure("invalid_sse") from None


def exercise(client, health):
    if not models_disabled(health):
        raise CheckFailure("exercise_requires_disabled_unconfigured_models")
    token, key = secrets.token_hex(32), secrets.token_hex(32)
    headers = {"X-Recommendation-Token": token, "Idempotency-Key": key}
    payload = {"query": "本地部署验收：寻找帝国兵种，不要大型重做", "limit": 3}
    code, _, job = client.request("/api/v1/recommendation-jobs", body=payload, headers=headers)
    require(
        code == 202 and re.fullmatch(r"[a-f0-9-]{36}", str(job.get("id", ""))),
        "invalid_job_response",
    )
    path = "/api/v1/recommendation-jobs/" + job["id"]
    _, _, duplicate = client.request("/api/v1/recommendation-jobs", body=payload, headers=headers)
    require(duplicate.get("id") == job["id"], "idempotency_failed")
    deadline = time.monotonic() + 40
    while job.get("status") in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.2)
        _, _, job = client.request(path, headers=headers, timeout=5)
    require(job.get("status") == "completed", "recommendation_worker_not_completed")
    result = job.get("result")
    require(isinstance(result, dict), "invalid_recommendation_result")
    require(result.get("engine") == "rules", "unexpected_model_engine")
    require(
        result.get("status") == "completed" and result.get("run_id") == job["id"],
        "invalid_recommendation_result",
    )
    require(
        isinstance(result.get("retrieval"), dict) and result["retrieval"].get("method") == "bm25",
        "unexpected_retrieval_method",
    )
    _, response_headers, raw = client.request(path + "/events", headers=headers, expect_json=False)
    require(response_headers.get_content_type() == "text/event-stream", "invalid_sse")
    require("no-store" in response_headers.get("Cache-Control", ""), "unsafe_sse_cache")
    require(token.encode() not in raw and key.encode() not in raw, "credential_echo")
    events = progress_events(raw)
    ids = [event_id for event_id, _ in events]
    require(
        ids == list(range(1, job.get("last_event_id", 0) + 1)) and len(ids) >= 3,
        "invalid_event_order",
    )
    stages = [snapshot.get("stage") for _, snapshot in events]
    require(stages[0] == "queued" and stages[-1] == "completed", "invalid_event_stages")
    require(
        all(stage in stages for stage in ("understand", "retrieve", "verify")),
        "invalid_event_stages",
    )
    _, _, tail = client.request(
        path + "/events", headers={**headers, "Last-Event-ID": str(ids[1])}, expect_json=False
    )
    require([event_id for event_id, _ in progress_events(tail)] == ids[2:], "event_replay_failed")
    for protected_path, probe_headers in (
        (path, {"X-Recommendation-Token": secrets.token_hex(32)}),
        ("/api/v1/runs/" + job["id"], {}),
    ):
        try:
            client.request(protected_path, headers=probe_headers)
        except CheckFailure as exc:
            require(exc.code == "http_404", "job_access_control_failed")
        else:
            raise CheckFailure("job_access_control_failed")
    return {
        "job_created": True,
        "engine": "rules",
        "retrieval": "bm25",
        "event_count": len(events),
        "provider_billing_verified": False,
    }


def running(base_url, *, run_exercise=False, require_data=False):
    checks = []
    client = LocalHTTP(base_url)
    health = None
    try:
        _, _, health = client.request("/api/v1/health")
        require(
            health.get("status") == "ok" and health.get("database") == "ok", "unhealthy_database"
        )
        checks.append(item("api_health", "passed", "前端代理能够访问 API，数据库连接正常。"))
        schema = health.get("schema")
        if not isinstance(schema, dict):
            checks.append(
                item("schema", "failed", "健康响应缺少数据库版本；请更新至 v0.6 并执行迁移检查。")
            )
        elif (
            schema.get("status") != "current"
            or not isinstance(schema.get("current_revision"), str)
            or not schema["current_revision"]
            or schema.get("current_revision") != schema.get("head_revision")
        ):
            checks.append(
                item("schema", "failed", "数据库版本未到当前版本；先备份，再执行显式升级。")
            )
        else:
            checks.append(item("schema", "passed", "数据库版本与当前代码要求一致。"))
    except CheckFailure as exc:
        checks.append(
            item(
                "api_health",
                "failed",
                "API 健康检查未通过；检查 API、数据库与前端代理。",
                code=exc.code,
            )
        )
    try:
        _, _, stats = client.request("/api/v1/stats")
        total = stats.get("total_mods")
        require(type(total) is int and total >= 0, "invalid_catalog_stats")
        checks.append(
            item(
                "catalog",
                ("failed" if require_data else "warning") if total == 0 else "passed",
                "目录为空；先运行有上限的真实来源导入。"
                if total == 0
                else "目录已有记录；此检查不评估覆盖率或推荐质量。",
                total_mods=total,
            )
        )
    except CheckFailure as exc:
        checks.append(item("catalog", "failed", "无法读取目录统计。", code=exc.code))
    try:
        _, headers, body = client.request("/", expect_json=False)
        require(
            headers.get_content_type() == "text/html" and b"ModSage" in body,
            "invalid_frontend_html",
        )
        _, headers, image = client.request(
            "/media/warhammer3/official-key-art.jpg", expect_json=False
        )
        require(
            headers.get_content_type() == "image/jpeg" and image.startswith(b"\xff\xd8\xff"),
            "missing_game_art",
        )
        checks.append(item("frontend", "passed", "首页 HTML 与项目内战锤主视觉资源可访问。"))
    except CheckFailure as exc:
        checks.append(item("frontend", "failed", "前端页面或主视觉资源未通过检查。", code=exc.code))
    if run_exercise:
        if any(check["status"] in {"failed", "blocked"} for check in checks):
            checks.append(item("recommendation", "blocked", "基础检查未通过，未创建验收任务。"))
        else:
            try:
                evidence = exercise(client, health)
                checks.append(
                    item(
                        "recommendation",
                        "passed",
                        "验收任务、后台执行、幂等提交、SSE 续接及访问凭证检查通过。",
                        **evidence,
                    )
                )
            except CheckFailure as exc:
                checks.append(
                    item(
                        "recommendation",
                        "blocked"
                        if exc.code == "exercise_requires_disabled_unconfigured_models"
                        else "failed",
                        "推荐验收未通过；须关闭所有进程的模型调用并清空模型配置，核对 worker 后重试。",
                        code=exc.code,
                    )
                )
    return report(
        "running",
        checks,
        read_only=not run_exercise,
        local_http_requests=client.requests,
        provider_billing_verified=False,
        worker_configuration_verified=False,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "doctor", help="Check runtime, permissions and ports without changing files"
    )
    check.add_argument("--mode", choices=("native", "docker"), default="docker")
    check.add_argument("--project-root", type=Path, default=ROOT)
    live = subparsers.add_parser("running", help="Check an already running local deployment")
    live.add_argument("--base-url", default="http://127.0.0.1:3000")
    live.add_argument(
        "--require-data", action="store_true", help="Treat an empty catalog as failure"
    )
    live.add_argument(
        "--exercise",
        action="store_true",
        help="Create one retained diagnostic job; requires disabled, unconfigured model services",
    )
    args = parser.parse_args(argv)
    try:
        result = (
            doctor(args.mode, args.project_root)
            if args.command == "doctor"
            else running(args.base_url, run_exercise=args.exercise, require_data=args.require_data)
        )
    except CheckFailure as exc:
        result = report(
            args.command,
            [item("input", "blocked", "检查输入无效；只接受本机回环地址。", code=exc.code)],
        )
    except Exception:
        # Do not emit traceback/URL/subprocess output that could contain user secrets.
        result = report(
            args.command, [item("diagnostic", "failed", "检查未完成；核对运行环境及命令参数。")]
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] in {"failed", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
