"""Exercise the actual API against the explicit CI PostgreSQL database."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from modsage.config import Settings
from modsage.database import init_database, make_database
from modsage.embeddings import backfill_embeddings, embedding_status
from modsage.jobs import (
    LeaseLost,
    claim_job,
    enqueue_refresh,
    finish_job,
    heartbeat_claim,
)
from modsage.main import app
from modsage.models import (
    Mod,
    ModEmbedding,
    RecommendationEvent,
    RecommendationJob,
    RecommendationRun,
)
from modsage.recommendation_jobs import (
    RecommendationJobNotFound,
    RecommendationLeaseLost,
    RecommendationQueueFull,
    cancel_recommendation_job,
    claim_recommendation_job,
    enqueue_recommendation_job,
    finish_recommendation_job,
    get_recommendation_job,
    heartbeat_recommendation_claim,
    recommendation_events,
    retry_recommendation_job,
    run_recommendation_worker,
)
from modsage.recommendations import recommend


def verify_refresh_concurrency(database_url: str) -> None:
    """Exercise contention over independent PostgreSQL connections, without Steam calls."""
    engine, factory = make_database(database_url)
    init_database(engine)
    settings = Settings(_env_file=None, refresh_enabled=False, refresh_max_attempts=3)
    now = datetime.now(timezone.utc)
    key = f"ci-postgres:{uuid4()}"
    barrier = Barrier(4)

    def enqueue(_):
        barrier.wait(timeout=10)
        return enqueue_refresh(factory, settings, idempotency_key=key, now=now)

    with ThreadPoolExecutor(max_workers=4) as pool:
        queued = list(pool.map(enqueue, range(4)))
    assert len({job["id"] for job in queued}) == 1, "Duplicate keys created different jobs"
    assert sum(job["enqueued"] for job in queued) == 1, "Exactly one enqueue must create a job"

    claim_barrier = Barrier(4)

    def claim(index):
        claim_barrier.wait(timeout=10)
        return claim_job(factory, settings, worker_id=f"ci-worker-{index}", now=now)

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = [claimed for claimed in pool.map(claim, range(4)) if claimed is not None]
    assert len(claims) == 1, "Only one worker may own the catalog lease"
    original = claims[0]

    # Simulate a dead worker, reclaim its expired lease, then try stale writes.
    later = now + timedelta(seconds=settings.refresh_lease_seconds + 1)
    recovered = claim_job(factory, settings, worker_id="ci-recovery", now=later)
    assert recovered is not None and recovered.job_id == original.job_id
    assert recovered.token != original.token
    try:
        heartbeat_claim(factory, original, settings.refresh_lease_seconds, now=later)
    except LeaseLost:
        pass
    else:
        raise AssertionError("Expired owner renewed a lease after another worker reclaimed it")
    try:
        finish_job(factory, original, "succeeded", now=later)
    except LeaseLost:
        pass
    else:
        raise AssertionError("Expired owner committed a stale success")

    result = finish_job(factory, recovered, "failed", now=later)
    assert result["attempts"] == 2 and result["status"] == "failed"
    engine.dispose()
    print(
        "PostgreSQL queue smoke passed: duplicate enqueue, concurrent claim, lease recovery, fencing"
    )


async def verify_recommendation_jobs(database_url: str) -> None:
    """Exercise durable recommendation ownership and recovery on real SQL connections."""
    engine, factory = make_database(database_url)
    init_database(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        llm_api_key="",
        embedding_api_key="",
        retrieval_mode="bm25",
        recommendation_worker_enabled=False,
        recommendation_lease_seconds=5,
        recommendation_max_attempts=3,
    )
    ids = []
    token, key = uuid4().hex + uuid4().hex, uuid4().hex
    now = datetime.now(timezone.utc) - timedelta(seconds=60)
    try:
        # Different submissions race for one queue slot, not just the same unique key.
        capacity_barrier = Barrier(4)
        limited = settings.model_copy(update={"recommendation_queue_limit": 1})

        def capacity(index):
            capability = uuid4().hex + uuid4().hex
            capacity_barrier.wait(timeout=10)
            try:
                job = enqueue_recommendation_job(
                    factory,
                    limited,
                    f"CI capacity fixture {index}",
                    token=capability,
                    idempotency_key=uuid4().hex,
                )
                return job, capability
            except RecommendationQueueFull:
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            admitted = [entry for entry in pool.map(capacity, range(4)) if entry]
        ids.extend(entry[0]["id"] for entry in admitted)
        assert len(admitted) == 1, "Concurrent submissions exceeded the queue capacity"
        cancel_recommendation_job(factory, admitted[0][0]["id"], admitted[0][1])

        barrier = Barrier(4)

        def enqueue(_):
            barrier.wait(timeout=10)
            return enqueue_recommendation_job(
                factory,
                settings,
                "CI fixture: 寻找帝国兵种，不要大修",
                token=token,
                idempotency_key=key,
                now=now,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            submissions = list(pool.map(enqueue, range(4)))
        job_id = submissions[0]["id"]
        ids.append(job_id)
        assert {entry["id"] for entry in submissions} == {job_id}
        assert len(recommendation_events(factory, job_id, token)) == 1
        claim_barrier = Barrier(4)

        def claim(index):
            claim_barrier.wait(timeout=10)
            return claim_recommendation_job(
                factory, settings, worker_id=f"ci-recommendation-{index}", now=now
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = [entry for entry in pool.map(claim, range(4)) if entry]
        assert len(claims) == 1, "Concurrent workers claimed the same recommendation"
        original = claims[0]
        later = now + timedelta(seconds=6)
        recovered = claim_recommendation_job(
            factory, settings, worker_id="ci-recommendation-recovery", now=later
        )
        assert recovered and recovered.job_id == job_id and recovered.token != original.token
        for stale_operation in (
            lambda: heartbeat_recommendation_claim(factory, original, 5, now=later),
            lambda: finish_recommendation_job(factory, original, "failed", now=later),
        ):
            try:
                stale_operation()
            except RecommendationLeaseLost:
                pass
            else:
                raise AssertionError("A stale worker mutated the recovered task")
        failed = finish_recommendation_job(factory, recovered, "failed", now=later)
        assert failed["attempts"] == 2
        retried = retry_recommendation_job(factory, settings, job_id, token)
        assert retried["status"] == "queued" and retried["attempts"] == 2

        # An independent engine can pick up and finish the existing persistent job.
        restarted_engine, restarted_factory = make_database(database_url)
        try:
            outcome = await run_recommendation_worker(restarted_factory, settings, once=True)
            assert outcome["id"] == job_id and outcome["status"] == "completed"
        finally:
            restarted_engine.dispose()
        completed = get_recommendation_job(factory, job_id, token)
        assert completed["attempts"] == 3
        assert completed["result"]["retrieval"]["method"] == "bm25"
        with factory() as session:
            assert session.get(RecommendationRun, job_id) is None
        events = recommendation_events(factory, job_id, token)
        assert [event["id"] for event in events] == list(range(1, completed["last_event_id"] + 1))
        assert recommendation_events(factory, job_id, token, after=2) == events[2:]
        try:
            get_recommendation_job(factory, job_id, uuid4().hex)
        except RecommendationJobNotFound:
            pass
        else:
            raise AssertionError("A task accepted another browser's capability")

        cancellable = enqueue_recommendation_job(
            factory, settings, "CI cancellation fixture", token=token, idempotency_key=uuid4().hex
        )
        ids.append(cancellable["id"])
        active = claim_recommendation_job(factory, settings, worker_id="ci-cancel-race")
        assert active and active.job_id == cancellable["id"]
        cancelled = cancel_recommendation_job(factory, active.job_id, token)
        assert cancelled["status"] == "cancelled"
        try:
            finish_recommendation_job(
                factory,
                active,
                "completed",
                result={"run_id": active.job_id, "status": "completed", "items": []},
            )
        except RecommendationLeaseLost:
            pass
        else:
            raise AssertionError("A late worker result overwrote cancellation")
        assert get_recommendation_job(factory, active.job_id, token)["result"] is None
    finally:
        with factory.begin() as session:
            session.execute(delete(RecommendationEvent).where(RecommendationEvent.job_id.in_(ids)))
            session.execute(delete(RecommendationJob).where(RecommendationJob.id.in_(ids)))
        engine.dispose()
    print(
        "PostgreSQL recommendation smoke passed: admission capacity, idempotency, concurrent claim, "
        "lease recovery, stale-owner fencing, retry, persistent event replay, protected result, cancel race"
    )


async def verify_embedding_roundtrip(database_url: str) -> None:
    """Use explicit CI fixtures and a local fake provider, never an external model."""
    engine, factory = make_database(database_url)
    init_database(engine)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        llm_api_key="",
        retrieval_mode="hybrid",
        embedding_api_key="ci-fixture-only",
        embedding_base_url="https://example.invalid/v1",
        embedding_model="ci-fixture-v1",
        embedding_dimensions=2,
    )
    ids = ["900000000201", "900000000202"]
    run_ids = []

    class Provider:
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            return [[0.8, 0.6] for _ in texts]

    provider = Provider()
    try:
        with factory.begin() as session:
            for mod_id, title, category in zip(
                ids,
                ["CI Fixture Empire Artillery", "CI Fixture Empire Overhaul"],
                ["units", "overhaul"],
                strict=True,
            ):
                session.add(
                    Mod(
                        id=mod_id,
                        title=title,
                        category=category,
                        description="CI TEST FIXTURE: Empire artillery units.",
                        source_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}",
                        tags=["mod", "Empire", category],
                        content_hash=f"ci-{mod_id}",
                    )
                )
        first = await backfill_embeddings(factory, settings, limit=20, client=provider)
        assert first["generated"] == 2 and provider.calls == 1
        second = await backfill_embeddings(factory, settings, limit=20, client=provider)
        assert second["generated"] == 0 and provider.calls == 1
        assert embedding_status(factory, settings)["indexed"] == 2
        alternate = settings.model_copy(update={"embedding_model": "ci-fixture-v2"})
        assert embedding_status(factory, alternate)["indexed"] == 0
        separate = await backfill_embeddings(factory, alternate, limit=20, client=provider)
        assert separate["generated"] == 2
        with factory() as session:
            stored = session.scalars(select(ModEmbedding).where(ModEmbedding.mod_id.in_(ids))).all()
            assert len(stored) == 4 and len({row.namespace for row in stored}) == 2
            assert all(row.vector == [0.8, 0.6] and row.dimensions == 2 for row in stored)
        assert embedding_status(factory, settings)["indexed"] == 2
        result = await recommend(
            factory, settings, "帝国火炮，不要大修", 3, embedding_client=provider
        )
        run_ids.append(result["run_id"])
        assert result["retrieval"]["method"] == "hybrid_rrf"
        assert [item["mod"]["id"] for item in result["items"]] == [ids[0]]
        assert result["items"][0]["retrieval"]["channels"] == ["lexical", "semantic"]
        with factory.begin() as session:
            session.get(Mod, ids[0]).description += " Source changed after indexing."
        changed = await recommend(
            factory, settings, "帝国火炮，不要大修", 3, embedding_client=provider
        )
        run_ids.append(changed["run_id"])
        assert changed["retrieval"]["method"] == "bm25"
        assert changed["retrieval"]["stale_count"] == 1
        assert [item["mod"]["id"] for item in changed["items"]] == [ids[0]]
    finally:
        with factory.begin() as session:
            session.execute(delete(RecommendationRun).where(RecommendationRun.id.in_(run_ids)))
            session.execute(delete(ModEmbedding).where(ModEmbedding.mod_id.in_(ids)))
            session.execute(delete(Mod).where(Mod.id.in_(ids)))
        engine.dispose()
    print(
        "PostgreSQL embedding smoke passed: JSON vectors, namespace isolation, idempotent backfill, hybrid filtering, stale fallback"
    )


def main() -> None:
    database_url = os.environ.get("MODSAGE_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        raise SystemExit("Set MODSAGE_DATABASE_URL to the isolated PostgreSQL test database")
    verify_refresh_concurrency(database_url)
    asyncio.run(verify_recommendation_jobs(database_url))
    asyncio.run(verify_embedding_roundtrip(database_url))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        health.raise_for_status()
        stats = client.get("/api/v1/stats")
        stats.raise_for_status()
        response = client.post(
            "/api/v1/recommendations",
            json={"query": "寻找帝国兵种相关内容，不要大修", "limit": 3},
        )
        response.raise_for_status()
        run = response.json()
        assert run["run_id"], "Recommendation must have a persistent run ID"
        saved = client.get(f"/api/v1/runs/{run['run_id']}")
        saved.raise_for_status()
        assert saved.json()["run_id"] == run["run_id"]
        client.get("/api/v1/digests/latest").raise_for_status()
        sync = client.get("/api/v1/sync/status")
        sync.raise_for_status()
        status = sync.json()
        assert status["latest_job"]["status"] == "failed"
        assert "token" not in status["latest_job"] and "idempotency_key" not in status["latest_job"]
    print(
        "PostgreSQL API smoke passed: health, catalog stats, persisted run, digest, refresh status"
    )


if __name__ == "__main__":
    main()
