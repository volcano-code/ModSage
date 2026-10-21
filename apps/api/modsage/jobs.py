"""Database-backed refresh jobs; no transaction spans a provider request.

Every catalog mutation takes the global catalog lease and the job's fencing token
in the same transaction. A reclaimed worker cannot import, enrich or publish a
digest. Stage checkpoints make import retries idempotent. Model attempts are
recorded *before* sending: a crash can lose an answer, but cannot repeatedly bill
the same task item. Public status deliberately exposes neither tokens nor keys.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from modsage.catalog import iso
from modsage.digests import build_digest
from modsage.enrichment import apply_summary, generate_summary
from modsage.ingestion import import_items
from modsage.llm import ModelClient, ModelInvalidOutput, ModelUnavailable
from modsage.models import IngestionRun, Mod, RefreshJob, RefreshLease, RefreshWorker, utcnow
from modsage.sources.steam import SteamClient, SteamConfigurationError, SteamSourceError

TIMEZONE = ZoneInfo("Asia/Shanghai")
SORTS = ("updated", "newest", "popular")
TERMINAL = {"succeeded", "partial", "failed"}
log = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    """The token no longer owns both leases; further writes must stop."""


class EmptyDiscovery(RuntimeError):
    pass


class EmptyDetails(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: str
    token: str
    worker_id: str


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _insert(session, model):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


def job_summary(job: RefreshJob) -> dict:
    result = job.result or {}
    discovered = {item for ids in result.get("discovery", {}).values() for item in ids}
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "source": job.source,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "completed_at": iso(job.completed_at),
        "next_attempt_at": iso(job.next_attempt_at) if job.status == "retry_wait" else None,
        "error_code": job.error_code,
        "error_stage": job.error_stage,
        "counts": {
            "discovered": len(discovered),
            "imported": result.get("import", {}).get("imported", 0),
            "enriched": result.get("enrichment", {}).get("generated", 0),
        },
    }


def enqueue_refresh(
    session_factory,
    settings,
    *,
    idempotency_key=None,
    source=None,
    now=None,
    _reuse_existing_options=False,
) -> dict:
    now = now or utcnow()
    key = idempotency_key or f"manual:{uuid4()}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", key):
        raise ValueError(
            "Idempotency key must be 1–160 ASCII letters, digits, dots, colons or dashes"
        )
    source = source or settings.refresh_source
    if source not in {"api", "public"}:
        raise ValueError("Refresh source must be api or public")
    options = {
        "source": source,
        "limit_per_sort": settings.refresh_limit_per_sort,
        "enrich_limit": settings.refresh_enrich_limit,
        "request_budget_seconds": settings.refresh_request_budget_seconds,
        "max_attempts": settings.refresh_max_attempts,
    }
    with session_factory.begin() as session:
        created = session.scalar(
            _insert(session, RefreshJob)
            .values(
                id=str(uuid4()),
                idempotency_key=key,
                created_at=now,
                next_attempt_at=now,
                status="queued",
                stage="discovery",
                attempts=0,
                result={},
                **options,
            )
            .on_conflict_do_nothing(index_elements=[RefreshJob.idempotency_key])
            .returning(RefreshJob.id)
        )
        job = session.scalar(select(RefreshJob).where(RefreshJob.idempotency_key == key))
        if not _reuse_existing_options and any(
            getattr(job, field) != value for field, value in options.items()
        ):
            raise ValueError("An existing idempotency key has different refresh options")
        return {**job_summary(job), "enqueued": created is not None}


def _schedule_today(settings, now):
    return (
        _utc(now)
        .astimezone(TIMEZONE)
        .replace(
            hour=settings.refresh_hour, minute=settings.refresh_minute, second=0, microsecond=0
        )
    )


def next_scheduled_at(settings, now=None):
    if not settings.refresh_enabled:
        return None
    now = now or utcnow()
    scheduled = _schedule_today(settings, now)
    if scheduled <= _utc(now):
        scheduled += timedelta(days=1)
    return scheduled.astimezone(timezone.utc)


def enqueue_due_refresh(session_factory, settings, *, now=None):
    """Catch up today's due issue only; never backfill an unbounded history."""
    now = now or utcnow()
    scheduled = _schedule_today(settings, now)
    if not settings.refresh_enabled or _utc(now) < scheduled:
        return None
    return enqueue_refresh(
        session_factory,
        settings,
        idempotency_key=f"daily:{scheduled.date().isoformat()}",
        now=now,
        _reuse_existing_options=True,
    )


def claim_job(session_factory, settings, *, worker_id, now=None) -> Claim | None:
    now = now or utcnow()
    token = str(uuid4())
    until = now + timedelta(seconds=settings.refresh_lease_seconds)
    with session_factory.begin() as session:
        session.execute(
            _insert(session, RefreshLease)
            .values(name="catalog")
            .on_conflict_do_nothing(index_elements=[RefreshLease.name])
        )
        acquired = session.scalar(
            update(RefreshLease)
            .where(
                RefreshLease.name == "catalog",
                or_(RefreshLease.owner.is_(None), RefreshLease.expires_at <= now),
            )
            .values(owner=token, expires_at=until)
            .returning(RefreshLease.name)
        )
        if acquired is None:
            return None
        # An attempt is consumed on claim, including a process crash. Expired
        # last attempts therefore become terminal instead of running forever.
        expired = session.scalars(
            select(RefreshJob).where(
                RefreshJob.status == "running",
                RefreshJob.lease_until <= now,
                RefreshJob.attempts >= RefreshJob.max_attempts,
            )
        ).all()
        for job in expired:
            job.status = "partial" if job.result.get("import") else "failed"
            job.completed_at = now
            job.error_code = "LeaseExpired"
            job.error_stage = job.stage
            job.lease_owner = None
            job.lease_until = None
        due = or_(
            and_(
                RefreshJob.status.in_(["queued", "retry_wait"]),
                RefreshJob.next_attempt_at <= now,
            ),
            and_(RefreshJob.status == "running", RefreshJob.lease_until <= now),
        )
        job = session.scalar(
            select(RefreshJob)
            .where(due, RefreshJob.attempts < RefreshJob.max_attempts)
            .order_by(RefreshJob.created_at, RefreshJob.id)
            .limit(1)
        )
        if job is None:
            session.execute(
                update(RefreshLease)
                .where(RefreshLease.owner == token)
                .values(owner=None, expires_at=None)
            )
            return None
        job.status = "running"
        job.attempts += 1
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.lease_owner = token
        job.lease_until = until
        return Claim(job.id, token, worker_id)


def fence(session, claim: Claim, *, now=None) -> RefreshJob:
    """Acquire both write locks and validate ownership in the mutation transaction."""
    now = now or utcnow()
    catalog = session.scalar(
        update(RefreshLease)
        .where(
            RefreshLease.name == "catalog",
            RefreshLease.owner == claim.token,
            RefreshLease.expires_at > now,
        )
        .values(owner=claim.token)
        .returning(RefreshLease.name)
    )
    job_id = session.scalar(
        update(RefreshJob)
        .where(
            RefreshJob.id == claim.job_id,
            RefreshJob.status == "running",
            RefreshJob.lease_owner == claim.token,
            RefreshJob.lease_until > now,
        )
        .values(heartbeat_at=now)
        .returning(RefreshJob.id)
    )
    if catalog is None or job_id is None:
        raise LeaseLost("Refresh lease is no longer owned")
    return session.get(RefreshJob, claim.job_id)


def _release(session, claim):
    session.execute(
        update(RefreshLease)
        .where(RefreshLease.name == "catalog", RefreshLease.owner == claim.token)
        .values(owner=None, expires_at=None)
    )


def heartbeat_claim(session_factory, claim, lease_seconds, *, now=None):
    now = now or utcnow()
    until = now + timedelta(seconds=lease_seconds)
    with session_factory.begin() as session:
        job = fence(session, claim, now=now)
        job.lease_until = until
        session.execute(
            update(RefreshLease).where(RefreshLease.owner == claim.token).values(expires_at=until)
        )
        session.execute(
            update(RefreshWorker)
            .where(RefreshWorker.id == claim.worker_id)
            .values(last_seen_at=now)
        )


def _complete(session, job, claim, status, now, error_code=None, error_stage=None):
    job.status = status
    job.completed_at = now
    job.error_code = error_code
    job.error_stage = error_stage
    job.stage = "completed"
    job.lease_owner = None
    job.lease_until = None
    _release(session, claim)


def finish_job(session_factory, claim, status, *, now=None):
    """Fenced finalization, also useful to test leases without network operations."""
    if status not in TERMINAL:
        raise ValueError("Expected a terminal job status")
    now = now or utcnow()
    with session_factory.begin() as session:
        job = fence(session, claim, now=now)
        _complete(session, job, claim, status, now)
        return job_summary(job)


def _error_code(exc):
    if isinstance(exc, SteamConfigurationError):
        return "SourceNotConfigured", False
    if isinstance(exc, SteamSourceError):
        return "SourceUnavailable", True
    if isinstance(exc, EmptyDiscovery):
        return "EmptyDiscovery", True
    if isinstance(exc, EmptyDetails):
        return "EmptyDetails", True
    if isinstance(exc, TimeoutError):
        return "RequestBudgetExceeded", True
    if isinstance(exc, SQLAlchemyError):
        return "DatabaseError", True
    return "InternalError", True


def fail_job(session_factory, claim, exc, *, now=None):
    now = now or utcnow()
    code, retryable = _error_code(exc)
    with session_factory.begin() as session:
        job = fence(session, claim, now=now)
        job.error_code = code
        job.error_stage = job.stage
        if retryable and job.attempts < job.max_attempts:
            job.status = "retry_wait"
            job.next_attempt_at = now + timedelta(seconds=min(300, 15 * 2 ** (job.attempts - 1)))
        else:
            job.status = "partial" if job.result.get("import") else "failed"
            job.completed_at = now
        job.lease_owner = None
        job.lease_until = None
        _release(session, claim)
        return job_summary(job)


def _worker_seen(session_factory, worker_id, *, stopped=False, now=None):
    now = now or utcnow()
    with session_factory.begin() as session:
        statement = _insert(session, RefreshWorker).values(
            id=worker_id,
            started_at=now,
            last_seen_at=now,
            stopped_at=now if stopped else None,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[RefreshWorker.id],
                set_={"last_seen_at": now, "stopped_at": now if stopped else None},
            )
        )


def list_jobs(session_factory, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError("Job history limit must be 1..100")
    with session_factory() as session:
        return [
            job_summary(job)
            for job in session.scalars(
                select(RefreshJob)
                .order_by(RefreshJob.created_at.desc(), RefreshJob.id)
                .limit(limit)
            )
        ]


def sync_status(session_factory, settings, *, now=None):
    now = now or utcnow()
    with session_factory() as session:
        latest = session.scalar(
            select(RefreshJob).order_by(RefreshJob.created_at.desc(), RefreshJob.id).limit(1)
        )
        last_success = session.scalar(
            select(func.max(RefreshJob.completed_at)).where(RefreshJob.status == "succeeded")
        )
        latest_worker = session.scalar(
            select(RefreshWorker).order_by(RefreshWorker.last_seen_at.desc()).limit(1)
        )
        active = session.scalar(
            select(RefreshWorker)
            .where(RefreshWorker.stopped_at.is_(None))
            .order_by(RefreshWorker.last_seen_at.desc())
            .limit(1)
        )
        recent = (
            active
            if active
            and (_utc(now) - _utc(active.last_seen_at)).total_seconds()
            <= max(settings.refresh_lease_seconds, settings.refresh_poll_seconds * 3)
            else None
        )
        worker = recent or latest_worker
        worker_status = "unknown"
        if worker:
            worker_status = (
                "recently_seen" if recent else ("stopped" if worker.stopped_at else "stale")
            )
        return {
            "latest_job": job_summary(latest) if latest else None,
            "last_successful_refresh_at": iso(last_success),
            "schedule": {
                "enabled": settings.refresh_enabled,
                "timezone": "Asia/Shanghai",
                "time": f"{settings.refresh_hour:02d}:{settings.refresh_minute:02d}",
                "next_run_at": iso(next_scheduled_at(settings, now)),
            },
            "worker": {
                "status": worker_status,
                "last_seen_at": iso(worker.last_seen_at) if worker else None,
            },
        }


async def _import_stage(session_factory, settings, claim, source_factory):
    with session_factory.begin() as session:
        job = fence(session, claim)
        if job.result.get("import"):
            return
        source_mode, limit = job.source, job.limit_per_sort
        # Discovery has no catalog side effects. Repeat its bounded calls on
        # retry to restore the source client's verified classification and
        # dependency metadata; these may be absent from the details endpoint.
        checkpoint = copy.deepcopy(job.result)
        checkpoint["discovery"] = {}
        checkpoint["discovery_observations"] = {}
        job.result = checkpoint
    source = (
        source_factory() if source_factory else SteamClient(api_key=settings.steam_api_key or None)
    )
    try:
        for sort in SORTS:
            with session_factory.begin() as session:
                job = fence(session, claim)
                job.stage = "discovery"
            discover = source.query_files if source_mode == "api" else source.discover_public
            ids = await discover(limit=limit, sort=sort)
            # The real source adapter validates IDs and bounds results; retain a
            # hard upper bound here too so adapters cannot grow job payloads.
            ids = list(dict.fromkeys(ids))[:limit]
            with session_factory.begin() as session:
                job = fence(session, claim)
                checkpoint = copy.deepcopy(job.result)
                checkpoint.setdefault("discovery", {})[sort] = ids
                checkpoint["discovery_observations"][sort] = {
                    "empty": not ids,
                    "warning_count": len(getattr(source, "last_warnings", [])),
                }
                job.result = checkpoint
        with session_factory.begin() as session:
            job = fence(session, claim)
            checkpoint = copy.deepcopy(job.result)
            ids = list(
                dict.fromkeys(item for sort in SORTS for item in checkpoint["discovery"][sort])
            )
            if not ids:
                # Allow a later attempt to rediscover, instead of replaying a
                # permanently empty checkpoint through all remaining retries.
                job.result = {**checkpoint, "discovery": {}}
            else:
                job.stage = "details"
        if not ids:
            raise EmptyDiscovery()
        items = await source.get_details(ids)
        if not items:
            raise EmptyDetails()
        with session_factory.begin() as session:
            job = fence(session, claim)
            job.stage = "import"
        with session_factory.begin() as session:
            job = fence(session, claim)
            counts = import_items(session, items)
            checkpoint = copy.deepcopy(job.result)
            checkpoint["import"] = counts
            checkpoint["details"] = {
                "accepted": len(items),
                "skipped": max(0, len(ids) - len(items)),
            }
            checkpoint["warnings"] = (
                ["LimitedPublicDiscovery"] if source_mode == "public" else []
            ) + (["ItemsFilteredOrUnavailable"] if len(items) < len(ids) else [])
            selected = (
                list(
                    session.scalars(
                        select(Mod.id)
                        .where(
                            Mod.id.in_([item.id for item in items]), Mod.summary_status == "pending"
                        )
                        .order_by(Mod.id)
                        .limit(job.enrich_limit)
                    )
                )
                if job.enrich_limit
                else []
            )
            checkpoint["enrichment"] = {
                "selected_ids": selected,
                "attempted_ids": [],
                "completed_ids": [],
                "generated": 0,
                "errors": [],
            }
            job.result = checkpoint
            job.stage = "enrichment"
            # The import checkpoint and its audit record commit together.
            session.add(
                IngestionRun(
                    id=job.id,
                    started_at=job.started_at,
                    completed_at=utcnow(),
                    status="completed",
                    mode=f"refresh_{source_mode}",
                    requested_ids=ids,
                    imported_count=len(items),
                    warnings=checkpoint["warnings"],
                )
            )
    finally:
        await source.aclose()


async def _enrich_stage(session_factory, settings, claim, model):
    with session_factory.begin() as session:
        job = fence(session, claim)
        selected = list(job.result["enrichment"]["selected_ids"])
    model = model or ModelClient(settings)
    for mod_id in selected:
        with session_factory.begin() as session:
            job = fence(session, claim)
            checkpoint = copy.deepcopy(job.result)
            enrichment = checkpoint["enrichment"]
            if mod_id in enrichment["attempted_ids"]:
                continue
            job.stage = "enrichment"
            enrichment["attempted_ids"].append(mod_id)
            mod = session.get(Mod, mod_id)
            data = (mod.title, mod.description[:12000], mod.summary_hash) if mod else None
            job.result = checkpoint
        error = None
        try:
            if not settings.llm_configured:
                raise ModelUnavailable()
            if data is None:
                raise ModelInvalidOutput()
            title, description, expected_hash = data
            summary = await generate_summary(model, title, description)
            with session_factory.begin() as session:
                job = fence(session, claim)
                apply_summary(session, settings, mod_id, expected_hash, summary)
                checkpoint = copy.deepcopy(job.result)
                checkpoint["enrichment"]["generated"] += 1
                checkpoint["enrichment"]["completed_ids"].append(mod_id)
                job.result = checkpoint
        except (ModelUnavailable, ModelInvalidOutput, ValueError) as exc:
            error = (
                "ModelUnavailable" if isinstance(exc, ModelUnavailable) else "InvalidModelEvidence"
            )
        if error:
            with session_factory.begin() as session:
                job = fence(session, claim)
                checkpoint = copy.deepcopy(job.result)
                checkpoint["enrichment"]["completed_ids"].append(mod_id)
                checkpoint["enrichment"]["errors"].append({"mod_id": mod_id, "code": error})
                job.result = checkpoint
    with session_factory.begin() as session:
        job = fence(session, claim)
        checkpoint = copy.deepcopy(job.result)
        enrichment = checkpoint["enrichment"]
        # A crash after sending a paid request has an explicitly uncertain
        # result. Do not send it again under the same task's spending budget.
        for mod_id in set(enrichment["attempted_ids"]) - set(enrichment["completed_ids"]):
            enrichment["errors"].append({"mod_id": mod_id, "code": "EnrichmentInterrupted"})
            enrichment["completed_ids"].append(mod_id)
        enrichment["complete"] = True
        job.result = checkpoint
        job.stage = "digest"


async def _pipeline(session_factory, settings, claim, source_factory, model):
    await _import_stage(session_factory, settings, claim, source_factory)
    await _enrich_stage(session_factory, settings, claim, model)
    with session_factory.begin() as session:
        job = fence(session, claim)
        checkpoint = copy.deepcopy(job.result)
        digest = build_digest(session, refresh=True)
        checkpoint["digest"] = {"date": digest["date"], "generated_at": digest["generated_at"]}
        job.result = checkpoint
        issues = []
        if any(
            value["empty"] or (job.source == "api" and value["warning_count"])
            for value in checkpoint.get("discovery_observations", {}).values()
        ):
            issues.append({"stage": "discovery", "code": "PartialSourceCoverage"})
        if checkpoint.get("details", {}).get("skipped"):
            issues.append({"stage": "details", "code": "PartialSourceCoverage"})
        if checkpoint["enrichment"]["errors"]:
            issues.append({"stage": "enrichment", "code": "EnrichmentIncomplete"})
        checkpoint["issues"] = issues
        job.result = checkpoint
        _complete(
            session,
            job,
            claim,
            "partial" if issues else "succeeded",
            utcnow(),
            issues[-1]["code"] if issues else None,
            issues[-1]["stage"] if issues else None,
        )
        return job_summary(job)


async def run_claim(session_factory, settings, claim, *, source_factory=None, model=None):
    with session_factory() as session:
        budget = session.get(RefreshJob, claim.job_id).request_budget_seconds

    async def keep_lease():
        while True:
            await asyncio.sleep(settings.refresh_lease_seconds / 3)
            try:
                heartbeat_claim(session_factory, claim, settings.refresh_lease_seconds)
            except (LeaseLost, SQLAlchemyError):
                raise LeaseLost("Heartbeat could not confirm ownership") from None

    operation = asyncio.create_task(
        asyncio.wait_for(
            _pipeline(session_factory, settings, claim, source_factory, model),
            timeout=budget,
        )
    )
    heartbeat = asyncio.create_task(keep_lease())
    try:
        done, _ = await asyncio.wait({operation, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if operation in done:
            return await operation
        await heartbeat
    except LeaseLost:
        return {"id": claim.job_id, "status": "lease_lost"}
    except Exception as exc:
        try:
            return fail_job(session_factory, claim, exc)
        except LeaseLost:
            return {"id": claim.job_id, "status": "lease_lost"}
    finally:
        operation.cancel()
        heartbeat.cancel()
        await asyncio.gather(operation, heartbeat, return_exceptions=True)


async def run_worker(session_factory, settings, *, once=False, source_factory=None, model=None):
    worker_id = str(uuid4())
    try:
        while True:
            _worker_seen(session_factory, worker_id)
            enqueue_due_refresh(session_factory, settings)
            claim = claim_job(session_factory, settings, worker_id=worker_id)
            result = (
                await run_claim(
                    session_factory,
                    settings,
                    claim,
                    source_factory=source_factory,
                    model=model,
                )
                if claim
                else {"status": "idle"}
            )
            if once:
                return result
            if claim:
                log.info("Refresh job %s finished attempt: %s", claim.job_id, result["status"])
            await asyncio.sleep(settings.refresh_poll_seconds)
    finally:
        _worker_seen(session_factory, worker_id, stopped=True)
