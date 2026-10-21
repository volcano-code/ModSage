"""Durable, capability-protected recommendation jobs and committed progress events.

Every state/event transition shares a transaction. Execution never holds a database
session across a provider await; completion is fenced against cancellation and lease
recovery. A recovered attempt reruns the bounded graph and can repeat an external
provider request. This is not exactly-once execution or a LangGraph checkpoint store.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from modsage.catalog import iso
from modsage.models import (
    RecommendationAdmission,
    RecommendationEvent,
    RecommendationJob,
    utcnow,
)
from modsage.recommendations import execute_recommendation

TERMINAL = {"completed", "failed", "cancelled"}
STAGES = {"understand", "retrieve", "verify"}
ERROR_CODES = {"RequestBudgetExceeded", "WorkerError", "LeaseExpired", "WorkerInterrupted"}
CAPABILITY = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
MAX_CURSOR = 2**31 - 1
log = logging.getLogger(__name__)


class RecommendationJobNotFound(RuntimeError):
    pass


class RecommendationJobConflict(RuntimeError):
    pass


class RecommendationQueueFull(RuntimeError):
    pass


class RecommendationLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class RecommendationClaim:
    job_id: str
    token: str
    worker_id: str
    query: str
    limit: int
    request_timeout_seconds: float


def token_fingerprint(token: str) -> str:
    if not isinstance(token, str) or CAPABILITY.fullmatch(token) is None:
        raise ValueError("A 32–128 character URL-safe recommendation token is required")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def parse_event_cursor(value: str | None) -> int:
    if value is None:
        return 0
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value) or int(value) > MAX_CURSOR:
        raise ValueError("Event cursor must be a bounded non-negative integer")
    return int(value)


def _insert(session, model):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


def recommendation_job_snapshot(job: RecommendationJob) -> dict:
    return {
        "id": job.id,
        "query": job.query,
        "limit": job.limit,
        "status": job.status,
        "stage": job.stage,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "completed_at": iso(job.completed_at),
        "last_event_id": job.last_event_id,
        "error_code": job.error_code,
        "result": copy.deepcopy(job.result),
    }


def _event(session, job, now):
    snapshot = recommendation_job_snapshot(job)
    session.add(
        RecommendationEvent(
            job_id=job.id,
            sequence=job.last_event_id,
            created_at=now,
            data=snapshot,
        )
    )
    return snapshot


def _authorized(session, job_id, token) -> RecommendationJob:
    fingerprint = token_fingerprint(token)
    job = session.get(RecommendationJob, job_id)
    if job is None or not hmac.compare_digest(job.token_hash, fingerprint):
        raise RecommendationJobNotFound("推荐任务不存在或访问凭证无效。")
    return job


def _admission_lock(session, now):
    session.execute(
        _insert(session, RecommendationAdmission)
        .values(name="queue", touched_at=now)
        .on_conflict_do_nothing(index_elements=[RecommendationAdmission.name])
    )
    session.execute(
        update(RecommendationAdmission)
        .where(RecommendationAdmission.name == "queue")
        .values(touched_at=now)
    )


def _check_capacity(session, settings):
    active = session.scalar(
        select(func.count())
        .select_from(RecommendationJob)
        .where(RecommendationJob.status.in_(["queued", "running"]))
    )
    if active >= settings.recommendation_queue_limit:
        raise RecommendationQueueFull("推荐队列暂时已满，请稍后重试。")


def enqueue_recommendation_job(
    session_factory, settings, query, limit=5, *, token, idempotency_key, now=None
) -> dict:
    fingerprint = token_fingerprint(token)
    if not isinstance(idempotency_key, str) or CAPABILITY.fullmatch(idempotency_key) is None:
        raise ValueError("A 32–128 character URL-safe idempotency key is required")
    query = query.strip() if isinstance(query, str) else ""
    if not 2 <= len(query) <= 1000 or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Invalid recommendation request")
    now = now or utcnow()
    with session_factory.begin() as session:
        _admission_lock(session, now)
        existing = session.scalar(
            select(RecommendationJob).where(
                RecommendationJob.token_hash == fingerprint,
                RecommendationJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.query != query or existing.limit != limit:
                raise RecommendationJobConflict("同一个请求标识不能用于不同的推荐内容。")
            return recommendation_job_snapshot(existing)
        _check_capacity(session, settings)
        job = RecommendationJob(
            id=str(uuid4()),
            token_hash=fingerprint,
            idempotency_key=idempotency_key,
            query=query,
            limit=limit,
            status="queued",
            stage="queued",
            attempts=0,
            max_attempts=settings.recommendation_max_attempts,
            request_timeout_seconds=settings.recommendation_timeout_seconds,
            created_at=now,
            last_event_id=1,
            result=None,
        )
        session.add(job)
        session.flush()
        return _event(session, job, now)


def get_recommendation_job(session_factory, job_id, token) -> dict:
    with session_factory() as session:
        return recommendation_job_snapshot(_authorized(session, job_id, token))


def recommendation_events(session_factory, job_id, token, *, after=0) -> list[dict]:
    if type(after) is not int or not 0 <= after <= MAX_CURSOR:
        raise ValueError("Invalid event cursor")
    with session_factory() as session:
        job = _authorized(session, job_id, token)
        if after > job.last_event_id:
            raise RecommendationJobConflict("事件游标超出当前记录，请重新读取任务状态。")
        events = session.scalars(
            select(RecommendationEvent)
            .where(RecommendationEvent.job_id == job_id, RecommendationEvent.sequence > after)
            .order_by(RecommendationEvent.sequence)
            .limit(100)
        ).all()
        return [{"id": row.sequence, "event": "progress", "data": row.data} for row in events]


def cancel_recommendation_job(session_factory, job_id, token, *, now=None) -> dict:
    now = now or utcnow()
    with session_factory.begin() as session:
        _authorized(session, job_id, token)
        job = session.scalar(
            update(RecommendationJob)
            .where(
                RecommendationJob.id == job_id,
                RecommendationJob.status.in_(["queued", "running"]),
            )
            .values(
                status="cancelled",
                stage="cancelled",
                completed_at=now,
                lease_token=None,
                lease_until=None,
                error_code=None,
                result=None,
                last_event_id=RecommendationJob.last_event_id + 1,
            )
            .returning(RecommendationJob)
            .execution_options(populate_existing=True)
        )
        if job is not None:
            return _event(session, job, now)
        session.expire_all()
        return recommendation_job_snapshot(session.get(RecommendationJob, job_id))


def retry_recommendation_job(session_factory, settings, job_id, token, *, now=None) -> dict:
    now = now or utcnow()
    with session_factory.begin() as session:
        _admission_lock(session, now)
        job = _authorized(session, job_id, token)
        if job.status in {"queued", "running"}:
            return recommendation_job_snapshot(job)
        if job.status != "failed":
            raise RecommendationJobConflict("只有失败任务可以重试，请为新的需求创建任务。")
        if job.attempts >= job.max_attempts:
            raise RecommendationJobConflict("该任务已达到重试上限，请检查问题后重新提交。")
        _check_capacity(session, settings)
        job = session.scalar(
            update(RecommendationJob)
            .where(
                RecommendationJob.id == job_id,
                RecommendationJob.status == "failed",
                RecommendationJob.attempts < RecommendationJob.max_attempts,
            )
            .values(
                status="queued",
                stage="queued",
                started_at=None,
                completed_at=None,
                lease_token=None,
                lease_until=None,
                error_code=None,
                result=None,
                last_event_id=RecommendationJob.last_event_id + 1,
            )
            .returning(RecommendationJob)
            .execution_options(populate_existing=True)
        )
        if job is None:
            session.expire_all()
            return recommendation_job_snapshot(session.get(RecommendationJob, job_id))
        return _event(session, job, now)


def _expired_exhausted(session, now):
    exhausted = and_(
        RecommendationJob.status == "running",
        RecommendationJob.lease_until <= now,
        RecommendationJob.attempts >= RecommendationJob.max_attempts,
    )
    ids = select(RecommendationJob.id).where(exhausted).limit(100)
    rows = session.scalars(
        update(RecommendationJob)
        .where(RecommendationJob.id.in_(ids), exhausted)
        .values(
            status="failed",
            stage="failed",
            completed_at=now,
            error_code="LeaseExpired",
            lease_token=None,
            lease_until=None,
            last_event_id=RecommendationJob.last_event_id + 1,
        )
        .returning(RecommendationJob)
    ).all()
    for job in rows:
        _event(session, job, now)


def claim_recommendation_job(session_factory, settings, *, worker_id, now=None):
    now = now or utcnow()
    token = str(uuid4())
    until = now + timedelta(seconds=settings.recommendation_lease_seconds)
    due = or_(
        RecommendationJob.status == "queued",
        and_(RecommendationJob.status == "running", RecommendationJob.lease_until <= now),
    )
    with session_factory.begin() as session:
        _expired_exhausted(session, now)
        candidate = (
            select(RecommendationJob.id)
            .where(due, RecommendationJob.attempts < RecommendationJob.max_attempts)
            .order_by(RecommendationJob.created_at, RecommendationJob.id)
            .limit(1)
        )
        if session.bind.dialect.name == "postgresql":
            candidate = candidate.with_for_update(skip_locked=True)
        job = session.scalar(
            update(RecommendationJob)
            .where(
                RecommendationJob.id.in_(candidate),
                due,
                RecommendationJob.attempts < RecommendationJob.max_attempts,
            )
            .values(
                status="running",
                stage="queued",
                attempts=RecommendationJob.attempts + 1,
                started_at=now,
                completed_at=None,
                heartbeat_at=now,
                lease_token=token,
                lease_until=until,
                error_code=None,
                result=None,
                last_event_id=RecommendationJob.last_event_id + 1,
            )
            .returning(RecommendationJob)
        )
        if job is None:
            return None
        _event(session, job, now)
        return RecommendationClaim(
            job.id, token, worker_id, job.query, job.limit, job.request_timeout_seconds
        )


def _fence(session, claim, now):
    job = session.scalar(
        update(RecommendationJob)
        .where(
            RecommendationJob.id == claim.job_id,
            RecommendationJob.status == "running",
            RecommendationJob.lease_token == claim.token,
            RecommendationJob.lease_until > now,
        )
        .values(heartbeat_at=now)
        .returning(RecommendationJob)
    )
    if job is None:
        raise RecommendationLeaseLost("Recommendation lease is no longer owned")
    return job


def heartbeat_recommendation_claim(session_factory, claim, lease_seconds, *, now=None):
    now = now or utcnow()
    with session_factory.begin() as session:
        job = _fence(session, claim, now)
        job.lease_until = now + timedelta(seconds=lease_seconds)


def advance_recommendation_stage(session_factory, claim, stage, *, now=None):
    if stage not in STAGES:
        raise ValueError("Unknown recommendation stage")
    now = now or utcnow()
    with session_factory.begin() as session:
        job = _fence(session, claim, now)
        if job.stage == stage:
            return recommendation_job_snapshot(job)
        job.stage = stage
        job.last_event_id += 1
        return _event(session, job, now)


def finish_recommendation_job(
    session_factory, claim, status, *, result=None, error_code=None, now=None
):
    if status not in {"completed", "failed"}:
        raise ValueError("Expected a completed or failed status")
    if status == "completed" and (
        not isinstance(result, dict)
        or result.get("run_id") != claim.job_id
        or result.get("status") != "completed"
    ):
        raise ValueError("A completed job requires its verified result")
    if error_code is not None and error_code not in ERROR_CODES:
        raise ValueError("Unknown safe error code")
    now = now or utcnow()
    with session_factory.begin() as session:
        job = _fence(session, claim, now)
        job.status = status
        job.stage = status
        job.completed_at = now
        job.result = result if status == "completed" else None
        job.error_code = None if status == "completed" else (error_code or "WorkerError")
        job.lease_token = None
        job.lease_until = None
        job.last_event_id += 1
        return _event(session, job, now)


def _release_interrupted(session_factory, claim):
    now = utcnow()
    with session_factory.begin() as session:
        job = _fence(session, claim, now)
        exhausted = job.attempts >= job.max_attempts
        job.status = "failed" if exhausted else "queued"
        job.stage = job.status
        job.completed_at = now if exhausted else None
        job.error_code = "WorkerInterrupted" if exhausted else None
        job.lease_token = None
        job.lease_until = None
        job.last_event_id += 1
        _event(session, job, now)


async def execute_recommendation_claim(
    session_factory, settings, claim, *, model=None, embedding_client=None
):
    async def stage(name):
        await asyncio.to_thread(advance_recommendation_stage, session_factory, claim, name)

    async def heartbeat():
        while True:
            await asyncio.sleep(min(1.0, settings.recommendation_lease_seconds / 3))
            await asyncio.to_thread(
                heartbeat_recommendation_claim,
                session_factory,
                claim,
                settings.recommendation_lease_seconds,
            )

    execution = asyncio.create_task(
        execute_recommendation(
            session_factory,
            settings.model_copy(
                update={"recommendation_timeout_seconds": claim.request_timeout_seconds}
            ),
            claim.query,
            claim.limit,
            run_id=claim.job_id,
            model=model,
            embedding_client=embedding_client,
            on_stage=stage,
        )
    )
    monitoring = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait({execution, monitoring}, return_when=asyncio.FIRST_COMPLETED)
        if execution not in done:
            await monitoring  # Raises on cancellation, lease loss or a database error.
        result = await execution
        completed = await asyncio.to_thread(
            finish_recommendation_job, session_factory, claim, "completed", result=result
        )
        return {"id": claim.job_id, "status": completed["status"]}
    except RecommendationLeaseLost:
        return {"id": claim.job_id, "status": "lease_lost"}
    except asyncio.CancelledError:
        execution.cancel()
        monitoring.cancel()
        await asyncio.gather(execution, monitoring, return_exceptions=True)
        try:
            await asyncio.to_thread(_release_interrupted, session_factory, claim)
        except (RecommendationLeaseLost, SQLAlchemyError):
            pass  # A cancelled/reclaimed job stays authoritative; expiry handles DB outages.
        raise
    except Exception as exc:
        code = "RequestBudgetExceeded" if isinstance(exc, TimeoutError) else "WorkerError"
        try:
            failed = await asyncio.to_thread(
                finish_recommendation_job, session_factory, claim, "failed", error_code=code
            )
            return {"id": claim.job_id, "status": failed["status"], "error_code": code}
        except RecommendationLeaseLost:
            return {"id": claim.job_id, "status": "lease_lost"}
    finally:
        execution.cancel()
        monitoring.cancel()
        await asyncio.gather(execution, monitoring, return_exceptions=True)


async def run_recommendation_worker(
    session_factory, settings, *, once=False, model=None, embedding_client=None
):
    """One claim at a time per process; leases permit multiple independent workers."""
    worker_id = str(uuid4())
    processed = 0
    while True:
        try:
            claim = await asyncio.to_thread(
                claim_recommendation_job, session_factory, settings, worker_id=worker_id
            )
            if claim is not None:
                outcome = await execute_recommendation_claim(
                    session_factory,
                    settings,
                    claim,
                    model=model,
                    embedding_client=embedding_client,
                )
                processed += 1
                if once:
                    return {**outcome, "processed": processed}
                continue
            if once:
                return {"status": "idle", "processed": processed}
        except SQLAlchemyError:
            # Never include SQL parameters, source text or credentials in logs.
            log.warning("Recommendation worker database operation failed; waiting to retry")
            if once:
                raise RuntimeError("Recommendation worker database operation failed") from None
        await asyncio.sleep(settings.recommendation_poll_seconds)
