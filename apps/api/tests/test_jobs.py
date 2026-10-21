import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from modsage import jobs
from modsage.digests import build_digest
from modsage.ingestion import import_items
from modsage.main import create_app
from modsage.models import (
    Digest,
    IngestionRun,
    MetricSnapshot,
    Mod,
    ModRelation,
    RefreshJob,
    RefreshWorker,
    SourceSnapshot,
    utcnow,
)
from modsage.sources.steam import APP_ID, SteamClient, SteamSourceError


class Source:
    def __init__(self, database, item_factory, *, discovery=None, detail_ids=None):
        self.database = database
        self.item_factory = item_factory
        self.discovery = discovery or {
            "updated": ["10001", "10002"],
            "newest": ["10002"],
            "popular": ["10001"],
        }
        self.detail_ids = detail_ids
        self.calls = []
        self.last_warnings = []
        self.closed = False

    async def discover_public(self, *, limit, sort):
        assert self.database.kw["bind"].pool.checkedout() == 0
        self.calls.append(sort)
        return self.discovery[sort][:limit]

    query_files = discover_public

    async def get_details(self, ids):
        assert self.database.kw["bind"].pool.checkedout() == 0
        self.calls.append("details")
        return [
            self.item_factory(item_id)
            for item_id in (self.detail_ids if self.detail_ids is not None else ids)
        ]

    async def aclose(self):
        self.closed = True


def public_settings(settings, **options):
    return settings.model_copy(update={"refresh_source": "public", **options})


def enqueue_and_claim(database, settings, *, key="fixture-job", now=None):
    queued = jobs.enqueue_refresh(database, settings, idempotency_key=key, now=now)
    claim = jobs.claim_job(database, settings, worker_id="fixture-worker", now=now)
    assert claim.job_id == queued["id"]
    return claim


def test_concurrent_enqueue_is_idempotent_and_detects_option_conflicts(database, settings):
    barrier = Barrier(4)

    def enqueue(_):
        barrier.wait()
        return jobs.enqueue_refresh(database, settings, idempotency_key="same-task")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enqueue, range(4)))
    assert len({result["id"] for result in results}) == 1
    assert sum(result["enqueued"] for result in results) == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 1
    with pytest.raises(ValueError, match="different refresh options"):
        jobs.enqueue_refresh(database, settings, idempotency_key="same-task", source="public")


def test_only_one_worker_can_claim_even_different_queued_jobs(database, settings):
    for key in ("first", "second"):
        jobs.enqueue_refresh(database, settings, idempotency_key=key)
    barrier = Barrier(4)

    def claim(index):
        barrier.wait()
        return jobs.claim_job(database, settings, worker_id=f"worker-{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    assert len([claim for claim in claims if claim]) == 1
    assert {job["status"] for job in jobs.list_jobs(database)} == {"running", "queued"}


def test_expired_owner_cannot_renew_finalize_or_mutate_catalog(database, settings, item_factory):
    now = utcnow()
    settings = settings.model_copy(update={"refresh_lease_seconds": 10})
    old = enqueue_and_claim(database, settings, now=now)
    new = jobs.claim_job(
        database, settings, worker_id="new-worker", now=now + timedelta(seconds=11)
    )
    assert new.job_id == old.job_id and new.token != old.token
    with pytest.raises(jobs.LeaseLost):
        jobs.heartbeat_claim(database, old, 10, now=now + timedelta(seconds=11))
    with pytest.raises(jobs.LeaseLost):
        jobs.finish_job(database, old, "succeeded", now=now + timedelta(seconds=11))
    with pytest.raises(jobs.LeaseLost), database.begin() as session:
        jobs.fence(session, old, now=now + timedelta(seconds=11))
        import_items(session, [item_factory()])
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Mod)) == 0
        assert session.get(RefreshJob, old.job_id).attempts == 2
    result = jobs.finish_job(database, new, "succeeded", now=now + timedelta(seconds=12))
    assert result["status"] == "succeeded"


def test_heartbeat_extends_both_leases_and_blocks_early_reclaim(database, settings):
    now = utcnow()
    settings = settings.model_copy(update={"refresh_lease_seconds": 10})
    claim = enqueue_and_claim(database, settings, now=now)
    jobs.heartbeat_claim(database, claim, 10, now=now + timedelta(seconds=8))
    assert (
        jobs.claim_job(database, settings, worker_id="other", now=now + timedelta(seconds=11))
        is None
    )
    recovered = jobs.claim_job(
        database, settings, worker_id="other", now=now + timedelta(seconds=19)
    )
    assert recovered.token != claim.token


def test_crash_on_last_attempt_becomes_terminal_when_lease_expires(database, settings):
    now = utcnow()
    settings = settings.model_copy(update={"refresh_max_attempts": 1})
    claim = enqueue_and_claim(database, settings, now=now)
    assert (
        jobs.claim_job(database, settings, worker_id="recovery", now=now + timedelta(minutes=2))
        is None
    )
    result = jobs.list_jobs(database)[0]
    assert result["id"] == claim.job_id
    assert (result["status"], result["error_code"]) == ("failed", "LeaseExpired")
    assert jobs.sync_status(database, settings)["last_successful_refresh_at"] is None


def test_retry_backoff_is_bounded_exhaustible_and_never_stores_provider_error(database, settings):
    now = utcnow()
    settings = settings.model_copy(update={"refresh_max_attempts": 2})
    first = enqueue_and_claim(database, settings, now=now)
    failure = SteamSourceError("provider body contains secret-api-key")
    result = jobs.fail_job(database, first, failure, now=now)
    assert result["status"] == "retry_wait"
    assert result["error_code"] == "SourceUnavailable"
    assert (
        jobs.claim_job(database, settings, worker_id="retry", now=now + timedelta(seconds=14))
        is None
    )
    second = jobs.claim_job(database, settings, worker_id="retry", now=now + timedelta(seconds=16))
    result = jobs.fail_job(database, second, failure, now=now + timedelta(seconds=16))
    assert result["status"] == "failed" and result["attempts"] == 2
    with database() as session:
        job = session.get(RefreshJob, first.job_id)
        assert "secret-api-key" not in json.dumps(job.result)
        assert "secret-api-key" not in job.error_code
    assert (
        jobs.claim_job(database, settings, worker_id="never", now=now + timedelta(days=1)) is None
    )


def test_schedule_uses_shanghai_date_and_only_catches_up_today(database, settings):
    settings = settings.model_copy(update={"refresh_enabled": True})
    before = datetime(2026, 10, 20, 23, 55, tzinfo=timezone.utc)
    due = before + timedelta(minutes=5)
    assert jobs.next_scheduled_at(settings, before) == due
    assert jobs.enqueue_due_refresh(database, settings, now=before) is None
    first = jobs.enqueue_due_refresh(database, settings, now=due)
    assert first["enqueued"] is True
    assert (
        jobs.enqueue_due_refresh(database, settings, now=due + timedelta(hours=2))["enqueued"]
        is False
    )
    assert jobs.next_scheduled_at(settings, due) == due + timedelta(days=1)
    jobs.enqueue_due_refresh(database, settings, now=due + timedelta(days=5, minutes=1))
    with database() as session:
        keys = set(session.scalars(select(RefreshJob.idempotency_key)))
    assert keys == {"daily:2026-10-21", "daily:2026-10-26"}
    disabled = settings.model_copy(update={"refresh_enabled": False})
    assert jobs.next_scheduled_at(disabled, due) is None
    assert jobs.enqueue_due_refresh(database, disabled, now=due) is None


async def test_worker_once_without_schedule_never_discovers_and_reports_stopped(database, settings):
    def forbidden():
        pytest.fail("An idle worker must not construct a source client")

    result = await jobs.run_worker(database, settings, once=True, source_factory=forbidden)
    assert result == {"status": "idle"}
    status = jobs.sync_status(database, settings)
    assert status["latest_job"] is None
    assert status["worker"]["status"] == "stopped"
    assert status["worker"]["last_seen_at"]


async def test_complete_refresh_deduplicates_imports_and_publishes_persisted_digest(
    database, settings, item_factory
):
    settings = public_settings(settings)
    queued = jobs.enqueue_refresh(database, settings, idempotency_key="same-daily")
    source = Source(database, item_factory)
    result = await jobs.run_worker(database, settings, once=True, source_factory=lambda: source)
    assert result["id"] == queued["id"]
    assert result["status"] == "succeeded"
    assert result["counts"] == {"discovered": 2, "imported": 2, "enriched": 0}
    assert source.calls == ["updated", "newest", "popular", "details"]
    assert source.closed
    with database() as session:
        for model, count in (
            (Mod, 2),
            (SourceSnapshot, 2),
            (MetricSnapshot, 2),
            (IngestionRun, 1),
            (Digest, 1),
        ):
            assert session.scalar(select(func.count()).select_from(model)) == count
    assert (
        jobs.enqueue_refresh(database, settings, idempotency_key="same-daily")["enqueued"] is False
    )
    assert (await jobs.run_worker(database, settings, once=True))["status"] == "idle"
    assert jobs.sync_status(database, settings)["last_successful_refresh_at"]


async def test_api_source_requires_key_and_does_not_silently_use_public(database, settings):
    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(database, settings, claim)
    assert (result["status"], result["error_code"]) == ("failed", "SourceNotConfigured")
    assert result["attempts"] == 1


@pytest.mark.parametrize("empty_stage", ["discovery", "details"])
async def test_empty_source_preserves_existing_catalog_and_cached_digest(
    database, settings, item_factory, empty_stage
):
    settings = public_settings(settings, refresh_max_attempts=1)
    with database.begin() as session:
        import_items(session, [item_factory("99999")])
        original_digest = build_digest(session)
    source = Source(
        database,
        item_factory,
        discovery={sort: [] for sort in jobs.SORTS} if empty_stage == "discovery" else None,
        detail_ids=[] if empty_stage == "details" else None,
    )
    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(database, settings, claim, source_factory=lambda: source)
    assert result["status"] == "failed"
    assert result["error_code"] == (
        "EmptyDiscovery" if empty_stage == "discovery" else "EmptyDetails"
    )
    assert jobs.sync_status(database, settings)["last_successful_refresh_at"] is None
    with database() as session:
        assert list(session.scalars(select(Mod.id))) == ["99999"]
        assert session.get(Digest, original_digest["date"]).result == original_digest


@pytest.mark.parametrize("incomplete", ["sort", "details", "query_warning"])
async def test_partial_source_coverage_is_not_reported_as_success(
    database, settings, item_factory, incomplete
):
    settings = public_settings(settings) if incomplete != "query_warning" else settings
    discovery = (
        {"updated": ["10001"], "newest": [], "popular": ["10001"]} if incomplete == "sort" else None
    )
    source = Source(
        database,
        item_factory,
        discovery=discovery,
        detail_ids=["10001"] if incomplete == "details" else None,
    )
    if incomplete == "query_warning":
        source.last_warnings = ["Unsafe upstream text must not be copied into the job"]
    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(database, settings, claim, source_factory=lambda: source)
    assert result["status"] == "partial"
    assert result["error_code"] == "PartialSourceCoverage"
    assert result["counts"]["imported"] >= 1
    assert jobs.sync_status(database, settings)["last_successful_refresh_at"] is None
    with database() as session:
        assert "Unsafe upstream" not in json.dumps(session.get(RefreshJob, claim.job_id).result)
        assert session.scalar(select(func.count()).select_from(Digest)) == 1


async def test_retry_after_import_does_not_duplicate_snapshots_or_model_attempts(
    database, settings, item_factory, monkeypatch
):
    settings = public_settings(
        settings, refresh_enrich_limit=1, llm_api_key="test-only", llm_model="fixture"
    )
    source = Source(database, item_factory)

    class Model:
        calls = 0

        async def json_response(self, system, data):
            assert database.kw["bind"].pool.checkedout() == 0
            self.calls += 1
            return {
                "summary_zh": "这个测试模组为帝国增加火炮兵种，介绍依据作者提供的描述。",
                "evidence_quotes": ["Adds artillery units for the Empire."],
            }

    model = Model()
    original = jobs.build_digest
    attempts = 0

    def fail_first_digest(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private simulated failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, "build_digest", fail_first_digest)
    claim = enqueue_and_claim(database, settings)
    first = await jobs.run_claim(
        database, settings, claim, source_factory=lambda: source, model=model
    )
    assert first["status"] == "retry_wait" and first["error_stage"] == "digest"
    second_claim = jobs.claim_job(
        database, settings, worker_id="retry", now=utcnow() + timedelta(seconds=16)
    )
    second = await jobs.run_claim(
        database,
        settings,
        second_claim,
        source_factory=lambda: pytest.fail("Import must not repeat"),
        model=model,
    )
    assert second["status"] == "succeeded"
    assert model.calls == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceSnapshot)) == 2
        assert session.scalar(select(func.count()).select_from(IngestionRun)) == 1
        assert session.get(Mod, "10001").summary_status == "generated"


async def test_interrupted_paid_attempt_is_not_sent_again_on_resume(
    database, settings, item_factory
):
    settings = public_settings(
        settings, refresh_enrich_limit=1, llm_api_key="test-only", llm_model="fixture"
    )
    source = Source(database, item_factory)

    class Model:
        calls = 0

        async def json_response(self, *_):
            self.calls += 1
            raise TimeoutError("provider may have accepted the paid request")

    model = Model()
    claim = enqueue_and_claim(database, settings)
    first = await jobs.run_claim(
        database, settings, claim, source_factory=lambda: source, model=model
    )
    assert first["status"] == "retry_wait"
    second_claim = jobs.claim_job(
        database, settings, worker_id="retry", now=utcnow() + timedelta(seconds=16)
    )
    second = await jobs.run_claim(database, settings, second_claim, model=model)
    assert second["status"] == "partial" and second["error_code"] == "EnrichmentIncomplete"
    assert model.calls == 1
    with database() as session:
        errors = session.get(RefreshJob, claim.job_id).result["enrichment"]["errors"]
        assert errors == [{"mod_id": "10001", "code": "EnrichmentInterrupted"}]


async def test_request_budget_cancels_source_and_records_safe_failure(
    database, settings, item_factory
):
    settings = public_settings(
        settings, refresh_max_attempts=1, refresh_request_budget_seconds=0.05
    )

    class HangingSource(Source):
        cancelled = False

        async def discover_public(self, **_):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    source = HangingSource(database, item_factory)
    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(database, settings, claim, source_factory=lambda: source)
    assert result["status"] == "failed" and result["error_code"] == "RequestBudgetExceeded"
    assert source.cancelled and source.closed


async def test_lost_heartbeat_cancels_work_without_old_owner_commits(
    database, settings, item_factory, monkeypatch
):
    settings = public_settings(settings, refresh_lease_seconds=0.3)

    class HangingSource(Source):
        cancelled = False

        async def discover_public(self, **_):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    def lost(*args, **kwargs):
        raise jobs.LeaseLost()

    monkeypatch.setattr(jobs, "heartbeat_claim", lost)
    source = HangingSource(database, item_factory)
    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(database, settings, claim, source_factory=lambda: source)
    assert result["status"] == "lease_lost"
    assert source.cancelled and source.closed
    with database() as session:
        assert session.get(RefreshJob, claim.job_id).status == "running"
        assert session.scalar(select(func.count()).select_from(Mod)) == 0


async def test_background_heartbeat_keeps_a_slow_request_owned(database, settings, item_factory):
    # Short leases are injected only for this test; production Settings require
    # at least ten seconds. The request deliberately outlasts its first lease.
    settings = public_settings(settings, refresh_lease_seconds=0.6)

    class SlowSource(Source):
        async def discover_public(self, **kwargs):
            if kwargs["sort"] == "updated":
                await asyncio.sleep(0.9)
            return await super().discover_public(**kwargs)

    source = SlowSource(database, item_factory)
    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(database, settings, claim, source_factory=lambda: source)
    assert result["status"] == "succeeded"
    with database() as session:
        job = session.get(RefreshJob, claim.job_id)
        assert (job.heartbeat_at - job.started_at).total_seconds() >= 0.8


async def test_opted_in_enrichment_without_model_keeps_import_but_is_partial(
    database, settings, item_factory
):
    settings = public_settings(settings, refresh_enrich_limit=1)
    source = Source(database, item_factory)

    class ForbiddenModel:
        async def json_response(self, *_):
            pytest.fail("Missing credentials must prevent a model request")

    claim = enqueue_and_claim(database, settings)
    result = await jobs.run_claim(
        database, settings, claim, source_factory=lambda: source, model=ForbiddenModel()
    )
    assert result["status"] == "partial"
    assert result["error_code"] == "EnrichmentIncomplete"
    assert result["counts"]["imported"] == 2
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Digest)) == 1
        assert session.get(Mod, "10001").summary_status == "pending"
    assert jobs.sync_status(database, settings)["last_successful_refresh_at"] is None


def test_status_distinguishes_stale_and_recent_workers_from_stopped_ones(database, settings):
    now = utcnow()
    with database.begin() as session:
        session.add(
            RefreshWorker(
                id="older-running",
                started_at=now - timedelta(hours=1),
                last_seen_at=now - timedelta(minutes=10),
            )
        )
    assert jobs.sync_status(database, settings, now=now)["worker"]["status"] == "stale"
    with database.begin() as session:
        session.get(RefreshWorker, "older-running").last_seen_at = now - timedelta(seconds=10)
        session.add(
            RefreshWorker(id="newer-stopped", started_at=now, last_seen_at=now, stopped_at=now)
        )
    # A freshly stopped --once process must not hide another active worker.
    assert jobs.sync_status(database, settings, now=now)["worker"]["status"] == "recently_seen"


async def test_real_adapter_restores_query_metadata_when_retrying_discovery(database, settings):
    attempt, queries = 0, []

    def handle(request):
        if request.url.path.endswith("QueryFiles/v1/"):
            query = json.loads(request.url.params["input_json"])["query_type"]
            queries.append((attempt, query))
            if attempt == 1 and query == 1:
                return httpx.Response(503, headers={"Retry-After": "10"})
            item_id = "10001" if query == 21 else "10002"
            return httpx.Response(
                200,
                json={
                    "response": {
                        "publishedfiledetails": [
                            {
                                "publishedfileid": item_id,
                                "file_type": 0,
                                "consumer_appid": APP_ID,
                                "children": [{"publishedfileid": "10099"}],
                                "num_children": 1,
                            }
                        ]
                    }
                },
            )
        ids = [
            value[0]
            for key, value in parse_qs(request.content.decode()).items()
            if key.startswith("publishedfileids[")
        ]
        return httpx.Response(
            200,
            json={
                "response": {
                    "publishedfiledetails": [
                        {
                            "publishedfileid": item_id,
                            "result": 1,
                            "consumer_app_id": APP_ID,
                            "title": f"Test fixture {item_id}",
                            "description": "Fixture source description.",
                        }
                        for item_id in ids
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:

        def source_factory():
            nonlocal attempt
            attempt += 1
            return SteamClient(api_key="test-only", client=client)

        claim = enqueue_and_claim(database, settings)
        first = await jobs.run_claim(database, settings, claim, source_factory=source_factory)
        assert first["status"] == "retry_wait"
        second_claim = jobs.claim_job(
            database, settings, worker_id="retry", now=utcnow() + timedelta(seconds=16)
        )
        second = await jobs.run_claim(
            database, settings, second_claim, source_factory=source_factory
        )
    assert second["status"] == "succeeded"
    assert (2, 21) in queries
    with database() as session:
        assert {mod.id: mod.dependencies_known for mod in session.scalars(select(Mod))} == {
            "10001": True,
            "10002": True,
        }
        assert session.scalar(select(func.count()).select_from(ModRelation)) == 2


def test_public_sync_status_is_honest_and_omits_private_job_parameters(settings):
    configured = settings.model_copy(
        update={"refresh_enabled": True, "steam_api_key": "secret-key"}
    )
    with TestClient(create_app(configured)) as client:
        empty = client.get("/api/v1/sync/status")
        assert empty.headers["Cache-Control"] == "no-store"
        assert empty.json()["worker"]["status"] == "unknown"
        assert empty.json()["schedule"]["enabled"] is True
        assert empty.json()["last_successful_refresh_at"] is None
        jobs.enqueue_refresh(
            client.app.state.session_factory, configured, idempotency_key="private-operator-key"
        )
        response = client.get("/api/v1/sync/status")
    assert response.status_code == 200
    body = response.json()
    assert body["latest_job"]["status"] == "queued"
    assert body["schedule"]["timezone"] == "Asia/Shanghai"
    assert "secret-key" not in response.text and "private-operator-key" not in response.text
    assert "lease_owner" not in response.text and "request_budget_seconds" not in response.text
