"""Durable, shared admission for bounded provider attempts and safe usage metadata.

Reservations are not refunded: a timed-out, cancelled, or crashed request can still
have been billed upstream. A reservation without a terminal record remains unknown,
and never counts as a verified provider success. No transaction spans network I/O.
"""

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from modsage.models import ProviderAdmission, ProviderCall, utcnow
from modsage.provider_config import provider_configuration, provider_fingerprint

OPERATIONS = {
    "understand",
    "summary",
    "refresh_summary",
    "query_embedding",
    "document_embedding",
    "canary_understand",
    "canary_summary",
    "canary_document_embedding",
    "canary_query_embedding",
}
ERROR_CODES = {
    "configuration_invalid",
    "not_configured",
    "unauthorized",
    "forbidden",
    "rate_limited",
    "timeout",
    "connection_error",
    "http_error",
    "invalid_json",
    "invalid_response",
    "invalid_output",
    "invalid_usage",
    "invalid_input",
    "input_too_large",
    "content_filtered",
    "output_truncated",
    "response_too_large",
    "unexpected_model",
    "cancelled",
    "provider_error",
    "provider_calls_disabled",
    "daily_call_limit",
    "minute_call_limit",
    "usage_unavailable",
}
FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call", "error"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class ProviderLimitExceeded(RuntimeError):
    """Safe operational code only: no database/provider exception details escape."""

    def __init__(self, code: str):
        self.code = code if code in ERROR_CODES else "usage_unavailable"
        super().__init__(self.code)


def _utc(value: datetime | None = None) -> datetime:
    value = value or utcnow()
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _window(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _safe_identifier(value, settings, *, model=False) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    # Identifiers may include model namespaces, but cannot contain URLs, whitespace,
    # query strings, arbitrary prose, or known credentials echoed by an upstream.
    pattern = (
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}" if model else r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}"
    )
    if not re.fullmatch(pattern, value) or "://" in value:
        return None
    for field in ("llm_api_key", "embedding_api_key", "steam_api_key", "admin_token"):
        secret = getattr(settings, field, "").strip()
        if secret and secret in value:
            return None
    return value


def _safe_int(value, *, maximum=2**31 - 1) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _calls_enabled(settings) -> bool:
    return bool(
        settings.provider_calls_enabled
        and settings.provider_daily_call_limit > 0
        and settings.provider_minute_call_limit > 0
    )


def _admission_lock(session, now: datetime) -> None:
    dialect = session.bind.dialect.name
    if dialect == "sqlite":
        insert = sqlite_insert
    elif dialect == "postgresql":
        insert = pg_insert
    else:
        raise ProviderLimitExceeded("usage_unavailable")
    session.execute(
        insert(ProviderAdmission)
        .values(name="provider_calls", touched_at=now)
        .on_conflict_do_nothing(index_elements=[ProviderAdmission.name])
    )
    # An UPDATE takes a row lock on PostgreSQL and a write lock on SQLite. Both
    # count and insert below happen before releasing it, including first-row races.
    session.execute(
        update(ProviderAdmission)
        .where(ProviderAdmission.name == "provider_calls")
        .values(touched_at=now)
    )


class ProviderUsageStore:
    def __init__(self, session_factory, settings):
        self.session_factory = session_factory
        self.settings = settings

    def reserve(self, provider: str, operation: str, *, now: datetime | None = None) -> str:
        if provider not in {"llm", "embedding"}:
            raise ValueError("Unknown provider kind")
        if operation not in OPERATIONS:
            raise ValueError("Unknown provider operation")
        if not self.settings.provider_calls_enabled:
            raise ProviderLimitExceeded("provider_calls_disabled")
        requested_now = now
        lock_at = _utc(now)
        call_id = str(uuid4())
        try:
            with self.session_factory.begin() as session:
                _admission_lock(session, lock_at)
                # A contender may wait for another transaction. Charge the actual
                # admission window, not the time it first began waiting for a lock.
                now = _utc(requested_now)
                start, end = _window(now)
                daily = session.scalar(
                    select(func.count())
                    .select_from(ProviderCall)
                    .where(ProviderCall.reserved_at >= start, ProviderCall.reserved_at < end)
                )
                if daily >= self.settings.provider_daily_call_limit:
                    raise ProviderLimitExceeded("daily_call_limit")
                minute = session.scalar(
                    select(func.count())
                    .select_from(ProviderCall)
                    .where(
                        ProviderCall.reserved_at > now - timedelta(seconds=60),
                    )
                )
                if minute >= self.settings.provider_minute_call_limit:
                    raise ProviderLimitExceeded("minute_call_limit")
                session.add(
                    ProviderCall(
                        id=call_id,
                        provider=provider,
                        operation=operation,
                        configuration_id=provider_fingerprint(self.settings, provider),
                        status="started",
                        reserved_at=now,
                    )
                )
        except SQLAlchemyError:
            raise ProviderLimitExceeded("usage_unavailable") from None
        return call_id

    def finish(
        self,
        call_id: str,
        *,
        status: str,
        latency_ms: int,
        error_code: str | None = None,
        http_status: int | None = None,
        returned_model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        request_id: str | None = None,
        finish_reason: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError("Unknown provider outcome")
        values = {
            "status": status,
            "completed_at": _utc(now),
            "latency_ms": _safe_int(latency_ms),
            "error_code": error_code
            if isinstance(error_code, str) and error_code in ERROR_CODES
            else ("provider_error" if error_code else None),
            "http_status": http_status
            if type(http_status) is int and 100 <= http_status <= 599
            else None,
            "returned_model": _safe_identifier(returned_model, self.settings, model=True),
            "request_id": _safe_identifier(request_id, self.settings),
            "finish_reason": finish_reason
            if isinstance(finish_reason, str) and finish_reason in FINISH_REASONS
            else None,
            "input_tokens": _safe_int(input_tokens),
            "output_tokens": _safe_int(output_tokens),
            "total_tokens": _safe_int(total_tokens),
        }
        try:
            with self.session_factory.begin() as session:
                result = session.execute(
                    update(ProviderCall)
                    .where(ProviderCall.id == call_id, ProviderCall.status == "started")
                    .values(**values)
                )
                return result.rowcount == 1
        except SQLAlchemyError:
            # The durable reservation remains spent and unknown, never refunded.
            raise ProviderLimitExceeded("usage_unavailable") from None


def _serialize(row: ProviderCall, settings) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "operation": row.operation,
        "status": row.status,
        "reserved_at": _iso(row.reserved_at),
        "completed_at": _iso(row.completed_at),
        "latency_ms": row.latency_ms,
        "http_status": row.http_status,
        "error_code": row.error_code,
        "returned_model": _safe_identifier(row.returned_model, settings, model=True),
        "request_id": _safe_identifier(row.request_id, settings),
        "finish_reason": row.finish_reason,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
    }


def usage_report(
    session_factory, settings, limit: int = 20, *, now: datetime | None = None
) -> dict:
    """Local/admin diagnostics, deliberately not exposed by a public HTTP route."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Usage history limit must be 1..100")
    now = _utc(now)
    start, end = _window(now)
    try:
        with session_factory() as session:
            rows = session.execute(
                select(
                    ProviderCall.status,
                    func.count(),
                    func.coalesce(func.sum(ProviderCall.input_tokens), 0),
                    func.coalesce(func.sum(ProviderCall.output_tokens), 0),
                    func.coalesce(func.sum(ProviderCall.total_tokens), 0),
                    func.count(ProviderCall.total_tokens),
                )
                .where(ProviderCall.reserved_at >= start, ProviderCall.reserved_at < end)
                .group_by(ProviderCall.status)
            ).all()
            minute = session.scalar(
                select(func.count())
                .select_from(ProviderCall)
                .where(
                    ProviderCall.reserved_at > now - timedelta(seconds=60),
                )
            )
            items = session.scalars(
                select(ProviderCall)
                .order_by(ProviderCall.reserved_at.desc(), ProviderCall.id.desc())
                .limit(limit)
            ).all()
    except SQLAlchemyError:
        raise ProviderLimitExceeded("usage_unavailable") from None
    today = {
        "reserved_calls": 0,
        "started": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
        "known_input_tokens": 0,
        "known_output_tokens": 0,
        "known_total_tokens": 0,
        "calls_without_total_tokens": 0,
    }
    for status, count, inputs, outputs, tokens, known in rows:
        today["reserved_calls"] += count
        if status in {"started", *TERMINAL_STATUSES}:
            today[status] = count
        today["known_input_tokens"] += inputs
        today["known_output_tokens"] += outputs
        today["known_total_tokens"] += tokens
        today["calls_without_total_tokens"] += count - known
    return {
        "status": "ok",
        "window": {
            "timezone": "UTC",
            "date": start.date().isoformat(),
            "start": _iso(start),
            "end": _iso(end),
        },
        "calls_enabled": _calls_enabled(settings),
        "limits": {
            "daily": settings.provider_daily_call_limit,
            "per_minute": settings.provider_minute_call_limit,
        },
        "today": today,
        "last_minute": {"reserved_calls": minute},
        "remaining": {
            "daily": max(0, settings.provider_daily_call_limit - today["reserved_calls"]),
            "per_minute": max(0, settings.provider_minute_call_limit - minute),
        },
        "items": [_serialize(row, settings) for row in items],
        "usage_semantics": "reported_only",
        "budget_semantics": "reserved_attempts_not_currency",
    }


def public_provider_status(session_factory, settings) -> dict:
    """Only current configuration and last protocol-level outcome, not business acceptance."""
    result = {}
    try:
        with session_factory() as session:
            for kind in ("llm", "embedding"):
                configuration = provider_configuration(settings, kind)
                row = None
                if configuration["configured"]:
                    row = session.scalar(
                        select(ProviderCall)
                        .where(
                            ProviderCall.provider == kind,
                            ProviderCall.configuration_id == provider_fingerprint(settings, kind),
                        )
                        .order_by(ProviderCall.reserved_at.desc(), ProviderCall.id.desc())
                        .limit(1)
                    )
                result[kind] = {
                    "configuration": configuration["state"],
                    "calls_enabled": _calls_enabled(settings),
                    "last_call": {
                        "status": row.status,
                        "at": _iso(row.reserved_at),
                        "completed_at": _iso(row.completed_at),
                        "error_code": row.error_code,
                    }
                    if row
                    else None,
                }
    except SQLAlchemyError:
        raise ProviderLimitExceeded("usage_unavailable") from None
    return result
