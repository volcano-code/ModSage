"""Exercise CLI/provider HTTP/usage contracts without external services or real keys.

Uses a loopback-only HTTP fixture and a newly created temporary SQLite database.
The catalogue is explicitly labelled CI fixture data and is deleted on exit.
This verifies integration, not a real provider, Chinese quality, or billing.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from modsage.config import Settings
from modsage.database import init_database, make_database
from modsage.ingestion import import_items
from modsage.models import Mod, ModEmbedding, RecommendationJob, RecommendationRun, utcnow
from modsage.sources.steam import SteamItem

SOURCE = "CI TEST FIXTURE. Adds artillery units for the Empire. No compatibility is claimed."
KEY = "ci-loopback-contract-key"


class ProviderFixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        assert self.headers.get("Authorization") == f"Bearer {KEY}"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(self.path)
        if self.path == "/v1/chat/completions":
            system = body["messages"][0]["content"]
            result = (
                {"factions": ["empire"], "excluded_categories": ["overhaul"]}
                if "Extract search constraints" in system
                else {
                    "summary_zh": "这是用于持续集成的测试条目。给定来源说明为帝国增加火炮单位，没有声明任何兼容性结论。",
                    "evidence_quotes": ["Adds artillery units for the Empire."],
                }
            )
            payload = {
                "model": "ci-chat-model",
                "choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
            }
        elif self.path == "/v1/embeddings":
            payload = {
                "model": "ci-embedding-model",
                "data": [
                    {"index": i, "embedding": [1.0, 0.0, 0.0]}
                    for i, _text in enumerate(body["input"])
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        else:
            self.send_error(404)
            return
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Request-ID", f"ci-call-{len(self.server.calls)}")
        self.end_headers()
        self.wfile.write(encoded)


def run_cli(environment, *args, expected_code=0):
    process = subprocess.run(
        [sys.executable, "-m", "modsage.cli", *args],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        cwd=Path(environment["MODSAGE_DATABASE_URL"].removeprefix("sqlite:///")).parent,
    )
    if process.returncode != expected_code:
        raise AssertionError(f"CLI {args[0]} returned unexpected exit code {process.returncode}")
    # Provider reports must not print secrets even under a successful fixture response.
    assert KEY not in process.stdout + process.stderr
    return json.loads(process.stdout)


def main():
    from sqlalchemy import func, select

    with tempfile.TemporaryDirectory(prefix="modsage-provider-contract-") as directory:
        database_url = f"sqlite:///{Path(directory) / 'isolated.db'}"
        settings = Settings(_env_file=None, database_url=database_url)
        engine, factory = make_database(settings.database_url)
        init_database(engine)
        now = utcnow()
        with factory.begin() as session:
            import_items(
                session,
                [
                    SteamItem(
                        id="900000000005",
                        title="CI FIXTURE Empire Artillery",
                        description=SOURCE,
                        author="CI fixture",
                        preview_url=None,
                        source_url="https://steamcommunity.com/sharedfiles/filedetails/?id=900000000005",
                        tags=["Mod", "Units", "Empire"],
                        published_at=now - timedelta(days=2),
                        updated_at=now,
                        fetched_at=now,
                        subscribers=None,
                        favorites=None,
                        required_ids=[],
                        raw={"fixture": "provider-contract-ci", "description": SOURCE},
                    )
                ],
            )
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderFixture)
        server.calls = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("MODSAGE_")
        }
        environment.update(
            {
                "MODSAGE_DATABASE_URL": database_url,
                "MODSAGE_STEAM_API_KEY": "",
                "MODSAGE_LLM_API_KEY": KEY,
                "MODSAGE_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "MODSAGE_LLM_MODEL": "ci-chat-model",
                "MODSAGE_EMBEDDING_API_KEY": KEY,
                "MODSAGE_EMBEDDING_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "MODSAGE_EMBEDDING_MODEL": "ci-embedding-model",
                "MODSAGE_EMBEDDING_RESPONSE_MODEL": "",
                "MODSAGE_EMBEDDING_DIMENSIONS": "3",
                "MODSAGE_PROVIDER_CALLS_ENABLED": "true",
                "MODSAGE_PROVIDER_DAILY_CALL_LIMIT": "4",
                "MODSAGE_PROVIDER_MINUTE_CALL_LIMIT": "4",
                "MODSAGE_RETRIEVAL_MODE": "bm25",
                "MODSAGE_REFRESH_ENABLED": "false",
                "MODSAGE_REFRESH_ENRICH_LIMIT": "0",
                "MODSAGE_RECOMMENDATION_WORKER_ENABLED": "false",
            }
        )
        try:
            preflight = run_cli(environment, "preflight")
            assert not server.calls
            offline = run_cli(environment, "provider-check", "--scope", "llm", expected_code=1)
            assert offline["status"] == "blocked"
            assert not server.calls
            llm = run_cli(environment, "provider-check", "--scope", "llm", "--live")
            embedding = run_cli(environment, "provider-check", "--scope", "embedding", "--live")
            assert llm["status"] == embedding["status"] == "succeeded"
            assert server.calls == [
                "/v1/chat/completions",
                "/v1/chat/completions",
                "/v1/embeddings",
                "/v1/embeddings",
            ]
            limited = run_cli(
                environment, "provider-check", "--scope", "llm", "--live", expected_code=1
            )
            assert limited["status"] in {"blocked", "failed"}
            assert len(server.calls) == 4
            usage = run_cli(environment, "usage")
            assert usage["today"]["reserved_calls"] == 4
            assert usage["today"]["succeeded"] == 4
            with factory() as session:
                assert session.get(Mod, "900000000005").summary_status == "pending"
                for model in [ModEmbedding, RecommendationJob, RecommendationRun]:
                    assert session.scalar(select(func.count()).select_from(model)) == 0
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "mode": "loopback_http_fixture_only",
                        "external_provider_calls": 0,
                        "fixture_http_calls": len(server.calls),
                        "preflight_status": preflight["status"],
                        "checks": [
                            "offline_no_network",
                            "live_opt_in",
                            "llm_canary",
                            "hybrid_canary",
                            "shared_quota",
                            "usage_metadata",
                            "production_catalog_unchanged",
                        ],
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            engine.dispose()


if __name__ == "__main__":
    main()
