"""Offline readiness and explicit, bounded checks against configured providers.

Live checks operate on a temporary copy of a few real catalog entries. Only the
shared usage ledger is written to the application database. No queued work,
production summary, vector, recommendation result, or source snapshot is changed.
"""

import asyncio
import copy
import re
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from modsage.database import init_database, make_database
from modsage.embeddings import EmbeddingClient, backfill_embeddings, embedding_status
from modsage.enrichment import generate_summary
from modsage.llm import ModelClient
from modsage.models import Mod, ModRelation, RecommendationJob, RefreshJob
from modsage.provider_config import provider_configuration
from modsage.provider_usage import ProviderUsageStore, usage_report
from modsage.recommendations import execute_recommendation
from modsage.sources.steam import SteamClient, SteamSourceError, normalize_id

SCOPES = {"llm", "embedding", "steam", "all"}
MAX_SAMPLE = 3
_CALL_FIELDS = (
    "call_id",
    "provider",
    "operation",
    "status",
    "error_code",
    "latency_ms",
    "http_status",
    "returned_model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "request_id",
    "finish_reason",
)


def _now():
    return datetime.now(UTC).isoformat()


def _count(session, model, *conditions):
    return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def preflight(session_factory, settings) -> dict:
    """Inspect local configuration and data only; never call a provider."""
    providers = {name: provider_configuration(settings, name) for name in ("llm", "embedding")}
    providers["steam"] = {
        "state": "configured" if settings.steam_api_key else "not_configured",
        "configured": bool(settings.steam_api_key),
        "missing_fields": [] if settings.steam_api_key else ["api_key"],
        "issues": [],
    }
    with session_factory() as session:
        catalog = {
            "total": _count(session, Mod),
            "with_source_description": _count(session, Mod, func.length(Mod.description) >= 20),
            "pending_summaries": _count(session, Mod, Mod.summary_status == "pending"),
        }
        queues = {
            "recommendations": _count(
                session, RecommendationJob, RecommendationJob.status.in_(["queued", "running"])
            ),
            "refreshes": _count(
                session, RefreshJob, RefreshJob.status.in_(["queued", "running", "retry_wait"])
            ),
        }
    usage = usage_report(session_factory, settings, limit=1)
    index = embedding_status(session_factory, settings)
    # Model and endpoint identities are deliberately absent from this readiness summary.
    index.pop("model", None)
    errors, warnings = [], []
    for name, state in providers.items():
        if state["state"] in {"incomplete", "invalid"}:
            errors.append({"scope": name, "code": "configuration_" + state["state"]})
    if not catalog["total"]:
        warnings.append("catalog_empty")
    if any(queues.values()):
        warnings.append("pending_jobs_may_call_providers_after_configuration")
    if settings.retrieval_mode == "hybrid":
        if not settings.embedding_configured:
            warnings.append("hybrid_will_fall_back_without_embedding_configuration")
        elif not index["indexed"]:
            warnings.append("hybrid_will_fall_back_without_current_vectors")
    sequential_budget = 0.0
    if settings.llm_configured:
        sequential_budget += settings.llm_timeout_seconds
    if settings.retrieval_mode == "hybrid" and settings.embedding_configured:
        sequential_budget += settings.embedding_timeout_seconds
    if sequential_budget + 2 > settings.recommendation_timeout_seconds:
        warnings.append("recommendation_timeout_below_provider_timeouts_plus_processing_margin")
    if settings.refresh_enabled and settings.refresh_source == "api" and not settings.steam_api_key:
        errors.append({"scope": "refresh", "code": "scheduled_steam_discovery_not_configured"})
    if settings.refresh_enrich_limit and not settings.llm_configured:
        warnings.append("scheduled_summaries_need_llm_configuration")
    if not settings.provider_calls_enabled:
        warnings.append("provider_calls_disabled")
    if min(usage["remaining"].values()) == 0:
        warnings.append("provider_call_quota_exhausted")
    return {
        "kind": "preflight",
        "schema_version": 1,
        "status": "failed" if errors else "ready",
        "network_calls": 0,
        "live_verified": False,
        "providers": providers,
        "catalog": catalog,
        "embedding_index": index,
        "pending_jobs": queues,
        "budgets": {
            "calls_enabled": settings.provider_calls_enabled,
            "limits": usage["limits"],
            "remaining": usage["remaining"],
            "timezone": "UTC",
            "currency_cost_verified": False,
        },
        "protocols": {
            "llm": "POST /chat/completions; JSON object in choices[0].message.content",
            "embedding": "POST /embeddings; indexed finite float vectors",
            "steam": "Authenticated QueryFiles plus public WARHAMMER III item details",
        },
        "errors": errors,
        "warnings": warnings,
        "next_step": "provider-check --scope llm|embedding|steam|all --live",
    }


def _call(client) -> dict:
    value = getattr(client, "last_call", None)
    if not isinstance(value, dict):
        return {}
    return {name: value.get(name) for name in _CALL_FIELDS}


def _real_success(call) -> bool:
    # Transport/JSON success alone is insufficient; every stage checks its own output too.
    return bool(call.get("call_id")) and call.get("status") == "succeeded"


def _error_code(exc) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        return code
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, SteamSourceError):
        return "steam_request_failed"
    return "validation_or_execution_failed"


def _safe_text(text, settings, limit):
    value = str(text)
    for secret in (
        settings.llm_api_key,
        settings.embedding_api_key,
        settings.steam_api_key,
        settings.admin_token,
    ):
        if secret:
            value = value.replace(secret, "[redacted]")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)[:limit]


def _sample(session_factory, mod_id):
    with session_factory() as session:
        statement = select(Mod).where(func.length(Mod.description) >= 20).order_by(Mod.id)
        if mod_id is not None:
            statement = statement.where(Mod.id == mod_id)
        mods = session.scalars(statement.limit(MAX_SAMPLE)).all()
        rows = [
            {
                column.name: copy.deepcopy(getattr(mod, column.name))
                for column in Mod.__table__.columns
            }
            for mod in mods
            if len(mod.description.strip()) >= 20
        ]
        ids = [row["id"] for row in rows]
        relations = session.scalars(select(ModRelation).where(ModRelation.mod_id.in_(ids))).all()
        relation_rows = [
            {
                column.name: copy.deepcopy(getattr(row, column.name))
                for column in ModRelation.__table__.columns
                if column.name != "id"
            }
            for row in relations
        ]
    return rows, relation_rows


def _stage(name, *, status="succeeded", code=None, **details):
    return {"name": name, "status": status, "error_code": code, **details}


async def _check_llm(factory, settings, store, sample, query, http):
    model = ModelClient(settings, http, usage_store=store, operation="canary_understand")
    checks = []
    try:
        result = await execute_recommendation(
            factory,
            settings.model_copy(update={"retrieval_mode": "bm25"}),
            query,
            MAX_SAMPLE,
            run_id=str(uuid.uuid4()),
            model=model,
        )
        call = _call(model)
        passed = (
            result.get("status") == "completed"
            and result.get("engine") == "llm"
            and bool(result.get("items"))
            and _real_success(call)
        )
        checks.append(
            _stage(
                "llm_recommendation",
                status="succeeded" if passed else "failed",
                code=None if passed else call.get("error_code") or "llm_not_exercised",
                engine=result.get("engine"),
                retrieval_method=result.get("retrieval", {}).get("method"),
                item_count=len(result.get("items", [])),
                call=call,
            )
        )
    except Exception as exc:
        checks.append(
            _stage("llm_recommendation", status="failed", code=_error_code(exc), call=_call(model))
        )
    if checks[-1]["status"] != "succeeded":
        checks.append(_stage("llm_summary", status="blocked", code="previous_check_failed"))
        return checks
    summary_model = ModelClient(settings, http, usage_store=store, operation="canary_summary")
    try:
        summary = await generate_summary(
            summary_model, sample["title"], sample["description"][:12000]
        )
        call = _call(summary_model)
        passed = _real_success(call)
        checks.append(
            _stage(
                "llm_summary",
                status="succeeded" if passed else "failed",
                code=None if passed else "llm_not_exercised",
                mod_id=sample["id"],
                source_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={sample['id']}",
                summary_zh=_safe_text(summary.summary_zh, settings, 1800),
                evidence_quotes=[_safe_text(q, settings, 300) for q in summary.evidence_quotes],
                structure_and_literal_quotes_validated=True,
                manual_review_required=True,
                semantic_faithfulness_verified=False,
                persisted=False,
                call=call,
            )
        )
    except Exception as exc:
        checks.append(
            _stage("llm_summary", status="failed", code=_error_code(exc), call=_call(summary_model))
        )
    return checks


async def _check_embedding(factory, settings, store, query, http):
    document_client = EmbeddingClient(
        settings, http, usage_store=store, operation="canary_document_embedding"
    )
    checks = []
    try:
        result = await backfill_embeddings(
            factory, settings, limit=MAX_SAMPLE, batch_size=MAX_SAMPLE, client=document_client
        )
        call = _call(document_client)
        passed = (
            result.get("status") == "completed" and result["generated"] > 0 and _real_success(call)
        )
        checks.append(
            _stage(
                "embedding_documents",
                status="succeeded" if passed else "failed",
                code=None if passed else call.get("error_code") or "embedding_not_exercised",
                generated=result["generated"],
                persisted=False,
                call=call,
            )
        )
    except Exception as exc:
        checks.append(
            _stage(
                "embedding_documents",
                status="failed",
                code=_error_code(exc),
                call=_call(document_client),
            )
        )
    if checks[-1]["status"] != "succeeded":
        checks.append(_stage("embedding_retrieval", status="blocked", code="previous_check_failed"))
        return checks
    query_client = EmbeddingClient(
        settings, http, usage_store=store, operation="canary_query_embedding"
    )
    # This check isolates the embedding service; it never silently calls a configured LLM.
    query_settings = settings.model_copy(
        update={"llm_api_key": "", "llm_model": "", "retrieval_mode": "hybrid"}
    )
    try:
        result = await execute_recommendation(
            factory,
            query_settings,
            query,
            MAX_SAMPLE,
            run_id=str(uuid.uuid4()),
            embedding_client=query_client,
        )
        call = _call(query_client)
        retrieval = result.get("retrieval", {})
        passed = (
            result.get("status") == "completed"
            and retrieval.get("method") == "hybrid_rrf"
            and retrieval.get("indexed_count", 0) > 0
            and bool(result.get("items"))
            and _real_success(call)
        )
        checks.append(
            _stage(
                "embedding_retrieval",
                status="succeeded" if passed else "failed",
                code=None if passed else call.get("error_code") or "hybrid_not_exercised",
                retrieval_method=retrieval.get("method"),
                indexed_count=retrieval.get("indexed_count", 0),
                item_count=len(result.get("items", [])),
                quality_benchmark_passed=False,
                call=call,
            )
        )
    except Exception as exc:
        checks.append(
            _stage(
                "embedding_retrieval",
                status="failed",
                code=_error_code(exc),
                call=_call(query_client),
            )
        )
    return checks


class _OnePageSteamClient(SteamClient):
    """A canary cannot spend its request budget walking additional discovery pages."""

    query_pages = 0
    api_operations = 0

    async def _json(self, method, path, **kwargs):
        if "QueryFiles" in path:
            if self.query_pages:
                raise SteamSourceError("Canary discovery is limited to one page")
            self.query_pages += 1
        if self.api_operations >= 2:
            raise SteamSourceError("Canary operation budget exhausted")
        self.api_operations += 1
        return await super()._json(method, path, **kwargs)


async def _check_steam(settings, http):
    steam = _OnePageSteamClient(api_key=settings.steam_api_key, client=http)
    started = time.monotonic()
    try:
        async with asyncio.timeout(45):
            ids = await steam.query_files(sort="updated", limit=1)
            if len(ids) != 1:
                return [_stage("steam_discovery", status="failed", code="steam_empty_discovery")]
            items = await steam.get_details(ids)
            passed = len(items) == 1 and items[0].id == ids[0]
            return [
                _stage(
                    "steam_discovery",
                    status="succeeded" if passed else "failed",
                    code=None if passed else "steam_no_valid_details",
                    discovered=len(ids),
                    verified_items=len(items),
                    item_ids=[item.id for item in items],
                    api_operations=steam.api_operations,
                    latency_ms=round((time.monotonic() - started) * 1000),
                    coverage_verified=False,
                    persisted=False,
                )
            ]
    except Exception as exc:
        return [
            _stage(
                "steam_discovery",
                status="failed",
                code=_error_code(exc),
                api_operations=steam.api_operations,
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        ]
    finally:
        await steam.aclose()


async def provider_check(
    session_factory,
    settings,
    *,
    scope="llm",
    live=False,
    mod_id=None,
    query=None,
    model_http=None,
    embedding_http=None,
    steam_http=None,
) -> dict:
    """An explicitly requested live contract check; missing work never counts as success.

    Optional HTTP clients are test injection points. The command-line interface
    never exposes them and always constructs the normal production adapters.
    """
    requested = {"llm", "embedding", "steam"} if scope == "all" else {scope}
    budget = {
        "max_llm_calls": 2 if "llm" in requested else 0,
        "max_embedding_calls": 2 if "embedding" in requested else 0,
        "max_steam_http_attempts": 6 if "steam" in requested else 0,
        "max_catalog_sample": MAX_SAMPLE,
    }
    report = {
        "kind": "provider_check",
        "schema_version": 1,
        "scope": scope if scope in SCOPES else "invalid",
        "status": "blocked",
        "live_requested": live,
        "started_at": _now(),
        "budget": budget,
        "checks": [],
        "errors": [],
        "warnings": [],
        "effects": {
            "catalog_written": False,
            "summaries_written": False,
            "vectors_written": False,
            "recommendation_results_written": False,
            "queued_jobs_executed": 0,
            "usage_records_written": False,
        },
    }

    def blocked(code, name="preconditions"):
        report["errors"].append({"scope": name, "code": code})

    if scope not in SCOPES:
        blocked("invalid_scope")
    if not live:
        blocked("live_flag_required")
    if query is not None and (not isinstance(query, str) or not 2 <= len(query.strip()) <= 1000):
        blocked("invalid_query")
    if mod_id is not None:
        try:
            mod_id = normalize_id(mod_id)
        except ValueError:
            blocked("invalid_mod_id")
    if report["errors"]:
        report["completed_at"] = _now()
        return report
    for name in sorted(requested - {"steam"}):
        state = provider_configuration(settings, name)
        if not state["configured"]:
            blocked("configuration_" + state["state"], name)
    if "steam" in requested and not settings.steam_api_key:
        blocked("configuration_not_configured", "steam")
    model_calls = budget["max_llm_calls"] + budget["max_embedding_calls"]
    if model_calls:
        if not settings.provider_calls_enabled:
            blocked("provider_calls_disabled")
        usage = usage_report(session_factory, settings, limit=1)
        if min(usage["remaining"].values()) < model_calls:
            blocked("insufficient_canary_call_budget")
    rows, relations = ([], [])
    if model_calls:
        rows, relations = _sample(session_factory, mod_id)
        if not rows:
            blocked("no_catalog_sample_with_source_description")
    if report["errors"]:
        report["completed_at"] = _now()
        return report
    engine = None
    try:
        if model_calls:
            engine, factory = make_database("sqlite:///:memory:")
            init_database(engine)
            with factory.begin() as session:
                session.add_all([Mod(**row) for row in rows])
                session.flush()
                session.add_all([ModRelation(**row) for row in relations])
            report["source_sample"] = {"ids": [row["id"] for row in rows], "count": len(rows)}
            check_query = query.strip() if query is not None else rows[0]["title"][:200]
            if len(check_query.strip()) < 2:
                check_query = "推荐这个目录中的模组"
            store = ProviderUsageStore(session_factory, settings)
            if "llm" in requested:
                report["checks"].extend(
                    await _check_llm(factory, settings, store, rows[0], check_query, model_http)
                )
            if "embedding" in requested:
                report["checks"].extend(
                    await _check_embedding(factory, settings, store, check_query, embedding_http)
                )
        if "steam" in requested:
            report["checks"].extend(await _check_steam(settings, steam_http))
    except Exception as exc:
        report["checks"].append(_stage("execution", status="failed", code=_error_code(exc)))
    finally:
        if engine is not None:
            engine.dispose()
    calls = [check["call"] for check in report["checks"] if check.get("call", {}).get("call_id")]
    report["effects"]["usage_records_written"] = bool(calls)
    report["usage"] = {
        "recorded_attempts": len(calls),
        "status": (
            "not_applicable"
            if not model_calls
            else "complete"
            if calls and all(call.get("total_tokens") is not None for call in calls)
            else "incomplete"
        ),
        "currency_cost_verified": False,
        "known_total_tokens": sum(call.get("total_tokens") or 0 for call in calls),
        "calls_without_total_tokens": sum(call.get("total_tokens") is None for call in calls),
    }
    if report["usage"]["calls_without_total_tokens"]:
        report["warnings"].append("provider_usage_missing_not_zero")
    report["status"] = (
        "succeeded"
        if report["checks"] and all(check["status"] == "succeeded" for check in report["checks"])
        else "failed"
    )
    report["errors"] = [
        {"scope": check["name"], "code": check["error_code"]}
        for check in report["checks"]
        if check["status"] != "succeeded"
    ]
    report["completed_at"] = _now()
    return report
