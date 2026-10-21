import asyncio
import json
import time
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import ClientDisconnect

from modsage.main import create_app
from modsage.recommendation_jobs import (
    claim_recommendation_job,
    finish_recommendation_job,
    run_recommendation_worker,
)

TOKEN = "fixture-capability-" + "a" * 46
KEY = "fixture-request-" + "b" * 48
HEADERS = {"X-Recommendation-Token": TOKEN, "Idempotency-Key": KEY}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def create(client, *, headers=None, query="帝国火炮，不要大修"):
    return client.post(
        "/api/v1/recommendation-jobs", json={"query": query}, headers=headers or HEADERS
    )


def parse_stream(text):
    events = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        ids = [line.removeprefix("id: ") for line in lines if line.startswith("id: ")]
        data = [line.removeprefix("data: ") for line in lines if line.startswith("data: ")]
        if ids:
            assert "event: progress" in lines
            events.append((int(ids[0]), json.loads(data[0])))
    return events


def test_create_and_retry_transport_are_idempotent_and_private(client):
    response = create(client)
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    job = response.json()
    path = "/api/v1/recommendation-jobs/" + job["id"]
    assert create(client).json() == job
    assert create(client, query="震旦兵种").status_code == 409
    assert client.get(path, headers=HEADERS).json() == job
    assert client.post(path + "/retry", headers=HEADERS).json() == job
    assert client.get("/api/v1/runs/" + job["id"]).status_code == 404
    assert TOKEN not in response.text and KEY not in response.text


@pytest.mark.parametrize(
    "suffix,method", [("", "get"), ("/events", "get"), ("/cancel", "post"), ("/retry", "post")]
)
def test_every_job_endpoint_requires_the_capability(client, suffix, method):
    identifier = create(client).json()["id"]
    path = f"/api/v1/recommendation-jobs/{identifier}{suffix}"
    call = getattr(client, method)
    assert call(path).status_code == 401
    assert call(path, headers={"X-Recommendation-Token": "z" * 64}).status_code == 404
    invalid = "this secret must not be echoed"
    denied = call(path, headers={"X-Recommendation-Token": invalid})
    assert denied.status_code == 401 and invalid not in denied.text
    assert (
        call(f"/api/v1/recommendation-jobs/{uuid4()}{suffix}", headers=HEADERS).status_code == 404
    )


def test_create_validates_keys_and_applies_shared_rate_limit(settings):
    configured = settings.model_copy(update={"request_limit_per_minute": 1})
    with TestClient(create_app(configured)) as client:
        assert create(client, headers={"Idempotency-Key": KEY}).status_code == 401
        job = create(client).json()
        limited = create(client, headers={**HEADERS, "Idempotency-Key": "z" * 64})
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
        # A rate limit on new work must not prevent stopping existing work.
        assert (
            client.post(f"/api/v1/recommendation-jobs/{job['id']}/cancel", headers=HEADERS).json()[
                "status"
            ]
            == "cancelled"
        )


def test_create_rejects_invalid_body_and_key(client):
    assert create(client, headers={"X-Recommendation-Token": TOKEN}).status_code == 422
    assert create(client, headers={**HEADERS, "Idempotency-Key": "short"}).status_code == 422
    assert create(client, query="   ").status_code == 422


@pytest.mark.parametrize("query", ["PRIVATE_QUERY_" * 100, {"secret": "PRIVATE_QUERY_VALUE"}])
def test_request_validation_does_not_echo_private_query(client, query):
    response = create(client, query=query)
    assert response.status_code == 422
    assert "PRIVATE_QUERY" not in response.text


def test_storage_failure_is_safe_retryable_http_response(client, monkeypatch):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("PRIVATE_QUERY_AND_TOKEN_IN_SQL_PARAMETERS")

    monkeypatch.setattr("modsage.recommendation_api.enqueue_recommendation_job", fail)
    response = create(client)
    assert response.status_code == 503 and response.headers["Retry-After"] == "2"
    assert "PRIVATE_QUERY" not in response.text


def test_queue_full_is_explicit_http_backpressure(settings):
    configured = settings.model_copy(update={"recommendation_queue_limit": 1})
    with TestClient(create_app(configured)) as client:
        assert create(client).status_code == 202
        response = create(client, headers={**HEADERS, "Idempotency-Key": "z" * 64})
        assert response.status_code == 429 and response.headers["Retry-After"] == "10"
        assert create(client).status_code == 202


def test_completed_sse_replays_ordered_actual_stages_and_resumes_exclusively(client, settings):
    job = create(client).json()
    factory = client.app.state.session_factory
    asyncio.run(run_recommendation_worker(factory, settings, once=True))
    path = f"/api/v1/recommendation-jobs/{job['id']}"
    snapshot = client.get(path, headers=HEADERS).json()
    stream = client.get(path + "/events", headers=HEADERS)
    assert stream.status_code == 200
    assert stream.headers["Content-Type"].startswith("text/event-stream")
    assert stream.headers["Cache-Control"] == "no-store"
    assert stream.headers["X-Accel-Buffering"] == "no"
    events = parse_stream(stream.text)
    assert [identifier for identifier, _ in events] == list(range(1, snapshot["last_event_id"] + 1))
    assert [event["stage"] for _, event in events] == [
        "queued",
        "queued",
        "understand",
        "retrieve",
        "verify",
        "completed",
    ]
    assert events[-1][1] == snapshot
    assert TOKEN not in stream.text and KEY not in stream.text
    assert client.get("/api/v1/runs/" + job["id"]).status_code == 404
    resumed = client.get(path + "/events?after=3", headers={**HEADERS, "Last-Event-ID": "2"})
    assert [identifier for identifier, _ in parse_stream(resumed.text)] == list(
        range(4, snapshot["last_event_id"] + 1)
    )
    at_end = client.get(
        path + "/events", headers={**HEADERS, "Last-Event-ID": str(snapshot["last_event_id"])}
    )
    assert parse_stream(at_end.text) == []


@pytest.mark.parametrize(
    "cursor,expected", [("-1", 422), ("01", 422), ("2147483648", 422), ("2", 409)]
)
def test_sse_rejects_bad_cursor_before_starting_stream(client, cursor, expected):
    job = create(client).json()
    response = client.get(
        f"/api/v1/recommendation-jobs/{job['id']}/events",
        headers={**HEADERS, "Last-Event-ID": cursor},
    )
    assert response.status_code == expected
    assert response.headers["Content-Type"].startswith("application/json")


def test_replay_contains_new_retry_after_historical_terminal_event(settings):
    configured = settings.model_copy(
        update={"recommendation_sse_seconds": 0.04, "recommendation_poll_seconds": 0.01}
    )
    with TestClient(create_app(configured)) as client:
        job = create(client).json()
        factory = client.app.state.session_factory
        claim = claim_recommendation_job(factory, configured, worker_id="fixture")
        finish_recommendation_job(factory, claim, "failed", error_code="WorkerError")
        path = f"/api/v1/recommendation-jobs/{job['id']}"
        retried = client.post(path + "/retry", headers=HEADERS)
        assert retried.status_code == 202 and retried.json()["status"] == "queued"
        response = client.get(path + "/events", headers=HEADERS)
        assert [event["status"] for _, event in parse_stream(response.text)] == [
            "queued",
            "running",
            "failed",
            "queued",
        ]
        assert client.get(path, headers=HEADERS).json()["status"] == "queued"


def test_embedded_worker_completes_without_a_long_lived_http_request(settings):
    configured = settings.model_copy(
        update={"recommendation_worker_enabled": True, "recommendation_poll_seconds": 0.01}
    )
    with TestClient(create_app(configured)) as client:
        response = create(client)
        assert response.status_code == 202
        path = f"/api/v1/recommendation-jobs/{response.json()['id']}"
        for _ in range(100):
            snapshot = client.get(path, headers=HEADERS).json()
            if snapshot["status"] == "completed":
                break
            time.sleep(0.01)
        assert snapshot["status"] == "completed" and snapshot["result"]["engine"] == "rules"
    # A new API lifespan retrieves the same committed snapshot and event log.
    with TestClient(create_app(settings)) as restored:
        assert restored.get(path, headers=HEADERS).json() == snapshot
        assert parse_stream(restored.get(path + "/events", headers=HEADERS).text)[-1][1] == snapshot


@pytest.mark.parametrize("failure", ["disconnect", "cancel"])
def test_sse_releases_slot_when_response_stops_before_first_body(settings, failure):
    configured = settings.model_copy(update={"recommendation_stream_limit": 1})
    with TestClient(create_app(configured)) as client:
        job = create(client).json()
        path = f"/api/v1/recommendation-jobs/{job['id']}"
        client.post(path + "/cancel", headers=HEADERS)
        route = next(
            route
            for route in client.app.routes
            if route.path == "/api/v1/recommendation-jobs/{job_id}/events"
        )

        async def exercise_transport():
            scope = {
                "type": "http",
                "asgi": {"spec_version": "2.4"},
                "headers": [(b"x-recommendation-token", TOKEN.encode())],
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            request = Request(scope, receive)
            response = await route.endpoint(UUID(job["id"]), request)
            started = asyncio.Event()

            async def failing_send(message):
                assert message["type"] == "http.response.start"
                if failure == "disconnect":
                    raise OSError("fixture client disconnected")
                started.set()
                await asyncio.Future()

            task = asyncio.create_task(response(scope, receive, failing_send))
            if failure == "cancel":
                await asyncio.wait_for(started.wait(), timeout=1)
                task.cancel()
            with pytest.raises(
                ClientDisconnect if failure == "disconnect" else asyncio.CancelledError
            ):
                await task

            # The same one-slot limiter admits another stream without waiting for a process restart.
            recovered = await route.endpoint(UUID(job["id"]), request)
            messages = []

            async def send(message):
                messages.append(message)

            await recovered(scope, receive, send)
            assert messages[0]["status"] == 200
            assert messages[-1] == {"type": "http.response.body", "body": b"", "more_body": False}

        asyncio.run(exercise_transport())
