import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from modsage.database import init_database, make_database
from modsage.ingestion import import_items
from modsage.models import RecommendationEvent, RecommendationJob, RecommendationRun, utcnow
from modsage.recommendation_jobs import (
    RecommendationJobConflict,
    RecommendationJobNotFound,
    RecommendationLeaseLost,
    RecommendationQueueFull,
    advance_recommendation_stage,
    cancel_recommendation_job,
    claim_recommendation_job,
    enqueue_recommendation_job,
    execute_recommendation_claim,
    finish_recommendation_job,
    get_recommendation_job,
    heartbeat_recommendation_claim,
    parse_event_cursor,
    recommendation_events,
    retry_recommendation_job,
    run_recommendation_worker,
)

TOKEN = "test-capability-" + "a" * 49
KEY = "test-request-" + "b" * 51


def enqueue(factory, settings, *, query="帝国火炮，不要大修", token=TOKEN, key=KEY, now=None):
    return enqueue_recommendation_job(
        factory, settings, query, token=token, idempotency_key=key, now=now
    )


def result_for(claim):
    return {
        "run_id": claim.job_id,
        "status": "completed",
        "engine": "rules",
        "constraints": {},
        "items": [],
        "warnings": [],
    }


def assert_ordered(factory, identifier, token=TOKEN):
    events = recommendation_events(factory, identifier, token)
    snapshot = get_recommendation_job(factory, identifier, token)
    assert [event["id"] for event in events] == list(range(1, snapshot["last_event_id"] + 1))
    assert events[-1]["data"] == snapshot
    return events


def test_enqueue_is_scoped_idempotent_and_protects_secrets(database, settings):
    first = enqueue(database, settings)
    assert enqueue(database, settings) == first
    with pytest.raises(RecommendationJobConflict):
        enqueue(database, settings, query="震旦兵种")
    another = enqueue(database, settings, token="z" * 64)
    assert another["id"] != first["id"]
    with pytest.raises(RecommendationJobNotFound):
        get_recommendation_job(database, first["id"], "z" * 64)
    assert first["status"] == "queued" and first["attempts"] == 0
    with database() as session:
        row = session.get(RecommendationJob, first["id"])
        assert row.token_hash == hashlib.sha256(TOKEN.encode()).hexdigest()
    public = str(assert_ordered(database, first["id"]))
    assert TOKEN not in public and KEY not in public and row.token_hash not in public


def test_enqueue_state_and_event_rollback_together(database, settings, monkeypatch):
    def fail(*args):
        raise RuntimeError("fixture event failure")

    monkeypatch.setattr("modsage.recommendation_jobs._event", fail)
    with pytest.raises(RuntimeError):
        enqueue(database, settings)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(RecommendationJob)) == 0
        assert session.scalar(select(func.count()).select_from(RecommendationEvent)) == 0


def test_concurrent_same_request_creates_one_job_and_event(database, settings):
    barrier = Barrier(4)

    def submit(_):
        barrier.wait(timeout=5)
        return enqueue(database, settings)

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(submit, range(4)))
    assert len({job["id"] for job in jobs}) == 1
    assert len(assert_ordered(database, jobs[0]["id"])) == 1


def test_queue_budget_is_atomic_and_does_not_reject_idempotent_replay(database, settings):
    configured = settings.model_copy(update={"recommendation_queue_limit": 1})
    barrier = Barrier(4)

    def submit(index):
        barrier.wait(timeout=5)
        try:
            return enqueue(database, configured, key=str(index) * 64)
        except RecommendationQueueFull:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(submit, range(4)))
    assert sum(job is not None for job in jobs) == 1
    first = next(job for job in jobs if job)
    with database() as session:
        key = session.get(RecommendationJob, first["id"]).idempotency_key
    assert enqueue(database, configured, key=key) == first
    cancel_recommendation_job(database, first["id"], TOKEN)
    assert enqueue(database, configured)["status"] == "queued"


def test_parallel_claim_only_one_owner(database, settings):
    job = enqueue(database, settings)
    barrier = Barrier(4)

    def claim(index):
        barrier.wait(timeout=5)
        return claim_recommendation_job(database, settings, worker_id=f"fixture-{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = [claim for claim in pool.map(claim, range(4)) if claim]
    assert len(claims) == 1 and claims[0].job_id == job["id"]
    assert get_recommendation_job(database, job["id"], TOKEN)["attempts"] == 1
    assert len(assert_ordered(database, job["id"])) == 2


def test_expired_owner_cannot_renew_advance_or_publish(database, settings):
    now = utcnow()
    job = enqueue(database, settings, now=now)
    old = claim_recommendation_job(database, settings, worker_id="first", now=now)
    later = now + timedelta(seconds=settings.recommendation_lease_seconds + 1)
    current = claim_recommendation_job(database, settings, worker_id="second", now=later)
    assert old.token != current.token
    assert current.job_id == old.job_id == job["id"]
    for operation in [
        lambda: heartbeat_recommendation_claim(database, old, 30, now=later),
        lambda: advance_recommendation_stage(database, old, "verify", now=later),
        lambda: finish_recommendation_job(
            database, old, "completed", result=result_for(old), now=later
        ),
    ]:
        with pytest.raises(RecommendationLeaseLost):
            operation()
    finished = finish_recommendation_job(
        database, current, "completed", result=result_for(current), now=later
    )
    assert finished["attempts"] == 2
    assert_ordered(database, job["id"])


def test_heartbeat_renews_lease_without_fabricating_progress(database, settings):
    now = utcnow()
    job = enqueue(database, settings, now=now)
    claim = claim_recommendation_job(database, settings, worker_id="first", now=now)
    tick = now + timedelta(seconds=settings.recommendation_lease_seconds - 1)
    heartbeat_recommendation_claim(database, claim, 30, now=tick)
    assert (
        claim_recommendation_job(
            database, settings, worker_id="second", now=tick + timedelta(seconds=2)
        )
        is None
    )
    assert len(assert_ordered(database, job["id"])) == 2


def test_expired_final_attempt_becomes_failed_once(database, settings):
    configured = settings.model_copy(update={"recommendation_max_attempts": 1})
    now = utcnow()
    job = enqueue(database, configured, now=now)
    claim_recommendation_job(database, configured, worker_id="lost", now=now)
    later = now + timedelta(seconds=configured.recommendation_lease_seconds + 1)
    assert claim_recommendation_job(database, configured, worker_id="new", now=later) is None
    failed = get_recommendation_job(database, job["id"], TOKEN)
    assert failed["status"] == "failed" and failed["error_code"] == "LeaseExpired"
    count = failed["last_event_id"]
    assert claim_recommendation_job(database, configured, worker_id="again", now=later) is None
    assert get_recommendation_job(database, job["id"], TOKEN)["last_event_id"] == count
    with pytest.raises(RecommendationJobConflict):
        retry_recommendation_job(database, configured, job["id"], TOKEN)


def test_cancel_fences_owner_and_is_idempotent(database, settings):
    job = enqueue(database, settings)
    claim = claim_recommendation_job(database, settings, worker_id="fixture")
    cancelled = cancel_recommendation_job(database, job["id"], TOKEN)
    assert cancelled["status"] == "cancelled" and cancelled["result"] is None
    assert cancel_recommendation_job(database, job["id"], TOKEN) == cancelled
    with pytest.raises(RecommendationLeaseLost):
        finish_recommendation_job(database, claim, "completed", result=result_for(claim))
    with pytest.raises(RecommendationJobConflict):
        retry_recommendation_job(database, settings, job["id"], TOKEN)
    assert_ordered(database, job["id"])


def test_cancel_and_finish_race_has_one_terminal_outcome(database, settings):
    job = enqueue(database, settings)
    claim = claim_recommendation_job(database, settings, worker_id="fixture")
    barrier = Barrier(2)

    def finish():
        barrier.wait(timeout=5)
        try:
            return finish_recommendation_job(database, claim, "completed", result=result_for(claim))
        except RecommendationLeaseLost:
            return None

    def cancel():
        barrier.wait(timeout=5)
        return cancel_recommendation_job(database, job["id"], TOKEN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = pool.submit(finish)
        cancelled = pool.submit(cancel)
        completed.result()
        cancelled.result()
    snapshot = get_recommendation_job(database, job["id"], TOKEN)
    assert snapshot["status"] in {"completed", "cancelled"}
    assert (snapshot["result"] is None) == (snapshot["status"] == "cancelled")
    history = assert_ordered(database, job["id"])
    assert sum(event["data"]["status"] in {"completed", "cancelled"} for event in history) == 1


def test_retry_keeps_history_and_consumes_total_attempt_budget(database, settings):
    configured = settings.model_copy(update={"recommendation_max_attempts": 2})
    job = enqueue(database, configured)
    for attempt in (1, 2):
        claim = claim_recommendation_job(database, configured, worker_id="fixture")
        assert (
            finish_recommendation_job(database, claim, "failed", error_code="WorkerError")[
                "attempts"
            ]
            == attempt
        )
        if attempt == 1:
            retry = retry_recommendation_job(database, configured, job["id"], TOKEN)
            assert retry["status"] == "queued" and retry["attempts"] == 1
            assert retry_recommendation_job(database, configured, job["id"], TOKEN) == retry
    with pytest.raises(RecommendationJobConflict):
        retry_recommendation_job(database, configured, job["id"], TOKEN)
    history = assert_ordered(database, job["id"])
    assert [event["data"]["status"] for event in history] == [
        "queued",
        "running",
        "failed",
        "queued",
        "running",
        "failed",
    ]


def test_state_and_events_survive_new_engine(database, settings):
    job = enqueue(database, settings)
    engine, factory = make_database(settings.database_url)
    try:
        init_database(engine)
        assert get_recommendation_job(factory, job["id"], TOKEN) == job
        assert len(assert_ordered(factory, job["id"])) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", ["-1", "1.0", "1e3", " 1", "+1", "01", "2147483648", "9" * 100])
def test_invalid_event_cursor_rejected(value):
    with pytest.raises(ValueError):
        parse_event_cursor(value)


def test_replay_is_exclusive_and_refuses_future_cursor(database, settings):
    job = enqueue(database, settings)
    cancel_recommendation_job(database, job["id"], TOKEN)
    tail = recommendation_events(database, job["id"], TOKEN, after=1)
    assert len(tail) == 1 and tail[0]["id"] == 2
    assert recommendation_events(database, job["id"], TOKEN, after=2) == []
    with pytest.raises(RecommendationJobConflict):
        recommendation_events(database, job["id"], TOKEN, after=3)
    assert parse_event_cursor(None) == 0
    assert parse_event_cursor("0") == 0


@pytest.mark.asyncio
async def test_worker_executes_real_graph_and_protected_result(database, settings, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory()])
    job = enqueue(database, settings)
    outcome = await run_recommendation_worker(database, settings, once=True)
    assert outcome == {"id": job["id"], "status": "completed", "processed": 1}
    saved = get_recommendation_job(database, job["id"], TOKEN)
    assert saved["result"]["engine"] == "rules"
    assert saved["result"]["items"][0]["mod"]["id"] == "10001"
    assert [event["data"]["stage"] for event in assert_ordered(database, job["id"])] == [
        "queued",
        "queued",
        "understand",
        "retrieve",
        "verify",
        "completed",
    ]
    with database() as session:
        assert session.get(RecommendationRun, job["id"]) is None
    assert await run_recommendation_worker(database, settings, once=True) == {
        "status": "idle",
        "processed": 0,
    }


@pytest.mark.asyncio
async def test_running_cancellation_interrupts_provider_and_publishes_nothing(database, settings):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Model:
        async def json_response(self, *args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    configured = settings.model_copy(update={"llm_api_key": "fixture", "llm_model": "fixture"})
    job = enqueue(database, configured)
    claim = claim_recommendation_job(database, configured, worker_id="fixture")
    execution = asyncio.create_task(
        execute_recommendation_claim(database, configured, claim, model=Model())
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    cancel_recommendation_job(database, job["id"], TOKEN)
    assert (await asyncio.wait_for(execution, timeout=3))["status"] == "lease_lost"
    assert cancelled.is_set()
    saved = get_recommendation_job(database, job["id"], TOKEN)
    assert saved["status"] == "cancelled" and saved["result"] is None
    assert_ordered(database, job["id"])


@pytest.mark.asyncio
async def test_timeout_and_unexpected_errors_are_safe_persisted_failures(database, settings):
    class Slow:
        async def json_response(self, *args):
            await asyncio.sleep(1)

    class Broken:
        async def json_response(self, *args):
            raise RuntimeError("DO_NOT_EXPOSE_PROVIDER_SECRET")

    for index, (model, budget, expected) in enumerate(
        [(Slow(), 0.02, "RequestBudgetExceeded"), (Broken(), 2, "WorkerError")]
    ):
        configured = settings.model_copy(
            update={
                "llm_api_key": "fixture",
                "llm_model": "fixture",
                "recommendation_timeout_seconds": budget,
            }
        )
        job = enqueue(database, configured, key=str(index) * 64)
        outcome = await run_recommendation_worker(database, configured, once=True, model=model)
        assert outcome["status"] == "failed" and outcome["error_code"] == expected
        saved = get_recommendation_job(database, job["id"], TOKEN)
        assert saved["result"] is None and saved["error_code"] == expected
        assert "DO_NOT_EXPOSE" not in str(assert_ordered(database, job["id"]))


@pytest.mark.asyncio
async def test_graceful_worker_stop_requeues_without_resetting_attempts(database, settings):
    entered = asyncio.Event()

    class Model:
        async def json_response(self, *args):
            entered.set()
            await asyncio.Event().wait()

    configured = settings.model_copy(update={"llm_api_key": "fixture", "llm_model": "fixture"})
    job = enqueue(database, configured)
    worker = asyncio.create_task(run_recommendation_worker(database, configured, model=Model()))
    await asyncio.wait_for(entered.wait(), timeout=2)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    interrupted = get_recommendation_job(database, job["id"], TOKEN)
    assert interrupted["status"] == "queued" and interrupted["attempts"] == 1
    await run_recommendation_worker(database, settings, once=True)
    restored = get_recommendation_job(database, job["id"], TOKEN)
    assert restored["status"] == "completed" and restored["attempts"] == 2
    assert_ordered(database, job["id"])


@pytest.mark.asyncio
async def test_worker_recovers_after_a_transient_claim_database_error(
    database, settings, monkeypatch
):
    import modsage.recommendation_jobs as jobs

    original = jobs.claim_recommendation_job
    attempts = 0

    def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("fixture secret must not be logged")
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, "claim_recommendation_job", flaky)
    configured = settings.model_copy(update={"recommendation_poll_seconds": 0.01})
    job = enqueue(database, configured)
    worker = asyncio.create_task(run_recommendation_worker(database, configured))
    try:
        for _ in range(100):
            if get_recommendation_job(database, job["id"], TOKEN)["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert get_recommendation_job(database, job["id"], TOKEN)["status"] == "completed"
        assert attempts >= 2
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
