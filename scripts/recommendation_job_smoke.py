"""Verify a local, model-free stack through its public proxy and durable worker."""

import argparse
import json
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def parse_events(body: str) -> list[dict]:
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line and not line.startswith(":"):
                name, _, value = line.partition(":")
                fields[name] = value.lstrip(" ")
        if "data" in fields:
            assert fields.get("event") == "progress"
            snapshot = json.loads(fields["data"])
            event_id = int(fields["id"])
            assert snapshot["last_event_id"] == event_id
            events.append({"id": event_id, "snapshot": snapshot})
    return events


def check_stack(base: str) -> dict:
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("This smoke check requires a local isolated stack origin")
    base = base.rstrip("/")
    token, key = secrets.token_hex(32), secrets.token_hex(32)

    def request(path, *, body=None, secret=None, extra=None, stream=False):
        headers = {"Accept": "text/event-stream" if stream else "application/json"}
        if secret:
            headers["X-Recommendation-Token"] = secret
        headers.update(extra or {})
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + "/api/v1" + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read(2 * 1024 * 1024).decode()
            assert token not in content and key not in content
            return response.status, response.headers, content if stream else json.loads(content)

    _, _, health = request("/health")
    if health["llm_configured"] or health.get("embedding_configured"):
        raise ValueError("Disable model credentials for this isolated smoke check")
    payload = {"query": "CI 验收：寻找帝国兵种，不要大修", "limit": 3}
    code, _, job = request(
        "/recommendation-jobs", body=payload, secret=token, extra={"Idempotency-Key": key}
    )
    assert code == 202
    path = "/recommendation-jobs/" + job["id"]
    _, _, replayed = request(
        "/recommendation-jobs", body=payload, secret=token, extra={"Idempotency-Key": key}
    )
    assert replayed["id"] == job["id"], "A repeated submission created another job"
    deadline = time.monotonic() + 30
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.1)
        _, _, job = request(path, secret=token)
    assert job["status"] == "completed", "The dedicated worker did not complete the queued job"
    assert job["result"]["status"] == "completed"
    assert job["result"]["retrieval"]["method"] == "bm25"
    assert job["result"]["run_id"] == job["id"]

    _, headers, body = request(path + "/events", secret=token, stream=True)
    assert headers.get_content_type() == "text/event-stream"
    assert "no-store" in headers["Cache-Control"]
    events = parse_events(body)
    ids = [event["id"] for event in events]
    assert ids == list(range(1, job["last_event_id"] + 1))
    stages = [event["snapshot"]["stage"] for event in events]
    assert stages[0] == "queued" and stages[-1] == "completed"
    assert all(stage in stages for stage in ["understand", "retrieve", "verify"])
    cursor = ids[1]
    _, _, tail = request(
        path + "/events", secret=token, extra={"Last-Event-ID": str(cursor)}, stream=True
    )
    assert [event["id"] for event in parse_events(tail)] == ids[2:]

    for denied_path, secret in [(path, secrets.token_hex(32)), ("/runs/" + job["id"], None)]:
        try:
            request(denied_path, secret=secret)
        except urllib.error.HTTPError as error:
            assert error.code == 404, "Protected records must not be publicly discoverable"
        else:
            raise AssertionError("A protected task or its result was accessible without ownership")
    return {"status": "passed", "events": len(events), "stages": stages, "provider_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    result = check_stack(args.base_url)
    print("Durable recommendation stack smoke passed: " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
