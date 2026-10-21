"""Exercise the actual API against the explicit CI PostgreSQL database."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient

from modsage.config import Settings
from modsage.database import init_database, make_database
from modsage.jobs import (
    LeaseLost,
    claim_job,
    enqueue_refresh,
    finish_job,
    heartbeat_claim,
)
from modsage.main import app


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


def main() -> None:
    database_url = os.environ.get("MODSAGE_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        raise SystemExit("Set MODSAGE_DATABASE_URL to the isolated PostgreSQL test database")
    verify_refresh_concurrency(database_url)
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
