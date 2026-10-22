import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from modsage.config import Settings
from modsage.database import make_database
from modsage.embeddings import EmbeddingClient, document_fingerprint, embedding_namespace
from modsage.ingestion import import_items
from modsage.llm import ModelClient
from modsage.models import Mod, ModEmbedding, ProviderCall, utcnow
from modsage.provider_usage import (
    ProviderLimitExceeded,
    ProviderUsageStore,
    public_provider_status,
    usage_report,
)
from modsage.recommendations import recommend

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def configured(settings, **updates):
    return settings.model_copy(
        update={
            "llm_api_key": "fixture-private-key",
            "llm_base_url": "https://provider.example/v1",
            "llm_model": "fixture-model",
            "embedding_api_key": "embedding-private-key",
            "embedding_base_url": "https://embedding.example/v1",
            "embedding_model": "embedding-model",
            "provider_calls_enabled": True,
            "provider_daily_call_limit": 200,
            "provider_minute_call_limit": 30,
            **updates,
        }
    )


def test_usage_is_persisted_and_only_known_tokens_are_summed(database, settings):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    first = store.reserve("llm", "understand", now=NOW)
    second = store.reserve("embedding", "query_embedding", now=NOW)
    assert store.finish(
        first,
        status="succeeded",
        latency_ms=210,
        http_status=200,
        returned_model="vendor/canonical-model",
        request_id="req-123",
        input_tokens=100,
        output_tokens=25,
        total_tokens=125,
        finish_reason="stop",
        now=NOW + timedelta(seconds=1),
    )
    assert store.finish(second, status="failed", latency_ms=50, error_code="timeout", now=NOW)
    report = usage_report(database, settings, now=NOW + timedelta(seconds=2))
    assert report["today"] == {
        "reserved_calls": 2,
        "started": 0,
        "succeeded": 1,
        "failed": 1,
        "cancelled": 0,
        "known_input_tokens": 100,
        "known_output_tokens": 25,
        "known_total_tokens": 125,
        "calls_without_total_tokens": 1,
    }
    assert report["remaining"] == {"daily": 198, "per_minute": 28}
    assert report["window"]["timezone"] == "UTC"
    assert report["usage_semantics"] == "reported_only"
    assert report["budget_semantics"] == "reserved_attempts_not_currency"
    assert "configuration_id" not in json.dumps(report)
    engine, restarted = make_database(settings.database_url)
    try:
        assert usage_report(restarted, settings, now=NOW)["today"]["reserved_calls"] == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "started"])
def test_failed_cancelled_and_unknown_attempts_never_refund_budget(database, settings, outcome):
    settings = configured(settings, provider_daily_call_limit=1)
    store = ProviderUsageStore(database, settings)
    first = store.reserve("llm", "summary", now=NOW)
    if outcome != "started":
        store.finish(first, status=outcome, latency_ms=1, now=NOW)
    with pytest.raises(ProviderLimitExceeded) as error:
        store.reserve("embedding", "document_embedding", now=NOW + timedelta(seconds=1))
    assert error.value.code == "daily_call_limit"
    assert usage_report(database, settings, now=NOW)["today"][outcome] == 1


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"provider_calls_enabled": False}, "provider_calls_disabled"),
        ({"provider_daily_call_limit": 0}, "daily_call_limit"),
        ({"provider_minute_call_limit": 0}, "minute_call_limit"),
    ],
)
def test_disabled_and_zero_limits_block_before_reservation(database, settings, updates, code):
    settings = configured(settings, **updates)
    with pytest.raises(ProviderLimitExceeded) as error:
        ProviderUsageStore(database, settings).reserve("llm", "understand", now=NOW)
    assert error.value.code == code
    assert usage_report(database, settings, now=NOW)["today"]["reserved_calls"] == 0
    assert usage_report(database, settings, now=NOW)["calls_enabled"] is False
    status = public_provider_status(database, settings)
    assert status["llm"]["calls_enabled"] is False
    assert status["embedding"]["calls_enabled"] is False


def test_minute_limit_is_shared_by_both_provider_kinds(database, settings):
    settings = configured(settings, provider_minute_call_limit=1)
    store = ProviderUsageStore(database, settings)
    store.reserve("llm", "understand", now=NOW)
    with pytest.raises(ProviderLimitExceeded, match="minute_call_limit"):
        store.reserve("embedding", "query_embedding", now=NOW + timedelta(seconds=59))
    store.reserve("embedding", "query_embedding", now=NOW + timedelta(seconds=60))
    report = usage_report(database, settings, now=NOW + timedelta(seconds=60))
    assert report["today"]["reserved_calls"] == 2
    assert report["last_minute"]["reserved_calls"] == 1


def test_late_admission_cannot_ignore_a_newer_concurrent_reservation(database, settings):
    settings = configured(settings, provider_minute_call_limit=1)
    store = ProviderUsageStore(database, settings)
    store.reserve("llm", "understand", now=NOW + timedelta(milliseconds=10))
    with pytest.raises(ProviderLimitExceeded, match="minute_call_limit"):
        store.reserve("embedding", "query_embedding", now=NOW)


def test_admission_uses_the_window_after_obtaining_the_lock(database, settings, monkeypatch):
    settings = configured(settings, provider_minute_call_limit=1)
    store = ProviderUsageStore(database, settings)
    store.reserve("llm", "understand", now=NOW)
    clock = iter([NOW + timedelta(seconds=59), NOW + timedelta(seconds=61)])
    monkeypatch.setattr("modsage.provider_usage.utcnow", lambda: next(clock))
    call_id = store.reserve("embedding", "query_embedding")
    with database() as session:
        row = session.get(ProviderCall, call_id)
        assert row.reserved_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=61)


def test_daily_limit_resets_at_utc_midnight_but_minute_limit_does_not(database, settings):
    settings = configured(settings, provider_daily_call_limit=1, provider_minute_call_limit=1)
    store = ProviderUsageStore(database, settings)
    midnight = NOW.replace(hour=0)
    store.reserve("llm", "understand", now=midnight - timedelta(seconds=10))
    with pytest.raises(ProviderLimitExceeded, match="minute_call_limit"):
        store.reserve("llm", "understand", now=midnight)
    store.reserve("embedding", "query_embedding", now=midnight + timedelta(seconds=50))
    report = usage_report(database, settings, now=midnight + timedelta(seconds=50))
    assert report["today"]["reserved_calls"] == 1
    assert report["remaining"]["daily"] == 0
    assert len(report["items"]) == 2


def test_finish_is_idempotent_and_does_not_create_unknown_calls(database, settings):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    call_id = store.reserve("llm", "understand", now=NOW)
    assert store.finish(call_id, status="cancelled", latency_ms=10, now=NOW)
    assert not store.finish(call_id, status="succeeded", latency_ms=20, now=NOW)
    assert not store.finish(str(uuid4()), status="succeeded", latency_ms=20, now=NOW)
    report = usage_report(database, settings, now=NOW)
    assert report["today"]["cancelled"] == 1
    assert report["today"]["succeeded"] == 0


def test_all_untrusted_metadata_is_validated_and_credential_echoes_are_discarded(
    database, settings
):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    call_id = store.reserve("llm", "canary_understand", now=NOW)
    store.finish(
        call_id,
        status="failed",
        latency_ms=-1,
        error_code="provider leaked fixture-private-key",
        http_status=900,
        returned_model="provider/fixture-private-key-v1",
        request_id="https://provider.example?key=embedding-private-key",
        finish_reason="user input copied here",
        input_tokens=True,
        output_tokens=-100,
        total_tokens=10**30,
        now=NOW,
    )
    report = usage_report(database, settings, now=NOW)
    row = report["items"][0]
    for field in (
        "latency_ms",
        "http_status",
        "returned_model",
        "request_id",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        assert row[field] is None
    assert row["error_code"] == "provider_error"
    serialized = json.dumps(report)
    assert "private-key" not in serialized and "provider.example" not in serialized


@pytest.mark.parametrize(
    "value", ["has spaces", "https://example.test/model", "x\nsecret", "x" * 201, "你好"]
)
def test_invalid_metadata_identifiers_are_not_saved(database, settings, value):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    call_id = store.reserve("llm", "understand", now=NOW)
    store.finish(call_id, status="succeeded", latency_ms=10, returned_model=value, request_id=value)
    item = usage_report(database, settings, now=NOW)["items"][0]
    assert item["returned_model"] is None
    assert item["request_id"] is None


def test_report_also_redacts_metadata_matching_newly_rotated_secrets(database, settings):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    call_id = store.reserve("llm", "understand", now=NOW)
    store.finish(call_id, status="succeeded", latency_ms=1, returned_model="formerly-public-id")
    rotated = configured(settings, llm_api_key="formerly-public-id")
    assert usage_report(database, rotated, now=NOW)["items"][0]["returned_model"] is None


def test_public_status_only_tracks_current_configuration_and_not_business_acceptance(
    database, settings
):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    first = store.reserve("llm", "understand", now=NOW)
    store.finish(first, status="succeeded", latency_ms=1, total_tokens=25, now=NOW)
    status = public_provider_status(database, settings)
    assert status["llm"]["configuration"] == "configured"
    assert status["llm"]["last_call"]["status"] == "succeeded"
    assert status["embedding"]["last_call"] is None
    assert set(status["llm"]) == {"configuration", "calls_enabled", "last_call"}
    serialized = json.dumps(status)
    for excluded in (
        "private-key",
        "fixture-model",
        "provider.example",
        "tokens",
        "request_id",
        "daily",
    ):
        assert excluded not in serialized
    rotated = configured(settings, llm_api_key="replacement-fixture-key")
    assert public_provider_status(database, rotated)["llm"]["last_call"] is None
    changed = configured(settings, llm_model="different-model")
    assert public_provider_status(database, changed)["llm"]["last_call"] is None
    unchanged = configured(settings, provider_daily_call_limit=5)
    assert public_provider_status(database, unchanged)["llm"]["last_call"]["status"] == "succeeded"


def test_latest_call_is_reported_even_if_previous_call_succeeded(database, settings):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    first = store.reserve("llm", "understand", now=NOW)
    store.finish(first, status="succeeded", latency_ms=1, now=NOW)
    second = store.reserve("llm", "summary", now=NOW + timedelta(seconds=1))
    status = public_provider_status(database, settings)
    assert status["llm"]["last_call"]["status"] == "started"
    store.finish(second, status="failed", latency_ms=1, error_code="unauthorized", now=NOW)
    status = public_provider_status(database, settings)
    assert status["llm"]["last_call"]["status"] == "failed"
    assert status["llm"]["last_call"]["error_code"] == "unauthorized"


@pytest.mark.parametrize("kind", ["llm", "embedding"])
def test_invalid_surrogate_key_has_safe_public_status_without_history(database, settings, kind):
    settings = configured(settings)
    store = ProviderUsageStore(database, settings)
    call_id = store.reserve(kind, "understand", now=NOW)
    store.finish(call_id, status="succeeded", latency_ms=1, now=NOW)
    invalid = settings.model_copy(update={f"{kind}_api_key": "fixture-secret-\ud800-invalid"})
    result = public_provider_status(database, invalid)
    assert result[kind]["configuration"] == "invalid"
    assert result[kind]["last_call"] is None
    serialized = json.dumps(result)
    assert "fixture-secret" not in serialized
    assert "\\ud800" not in serialized


class BrokenFactory:
    def begin(self):
        raise SQLAlchemyError("database password=fixture-private-key https://private.example")

    def __call__(self):
        return self.begin()


def test_database_failures_block_admission_and_have_safe_errors(settings):
    store = ProviderUsageStore(BrokenFactory(), configured(settings))
    for operation in (
        lambda: store.reserve("llm", "understand", now=NOW),
        lambda: store.finish(str(uuid4()), status="failed", latency_ms=10),
        lambda: usage_report(BrokenFactory(), configured(settings)),
        lambda: public_provider_status(BrokenFactory(), configured(settings)),
    ):
        with pytest.raises(ProviderLimitExceeded) as error:
            operation()
        assert str(error.value) == "usage_unavailable"
        assert error.value.__cause__ is None


def test_unsupported_operation_and_outcome_never_store_arbitrary_input(database, settings):
    store = ProviderUsageStore(database, configured(settings))
    for provider, operation in (("unknown", "understand"), ("llm", "this is a user prompt")):
        with pytest.raises(ValueError):
            store.reserve(provider, operation, now=NOW)
    with pytest.raises(ValueError):
        store.finish(str(uuid4()), status="arbitrary response", latency_ms=0)
    with database() as session:
        assert session.scalars(select(ProviderCall)).all() == []


@pytest.mark.parametrize("limit", [0, 101, True, "20"])
def test_usage_history_is_bounded(database, settings, limit):
    with pytest.raises(ValueError):
        usage_report(database, configured(settings), limit)


def test_concurrent_connections_cannot_overspend_the_last_allowance(database, settings):
    settings = configured(settings, provider_daily_call_limit=1)
    barrier = Barrier(4)

    def reserve(index):
        engine, factory = make_database(settings.database_url)
        try:
            barrier.wait(timeout=10)
            try:
                return ProviderUsageStore(factory, settings).reserve(
                    "llm" if index % 2 else "embedding", "understand", now=NOW
                )
            except ProviderLimitExceeded as exc:
                assert exc.code == "daily_call_limit"
                return None
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    assert sum(result is not None for result in results) == 1
    assert usage_report(database, settings, now=NOW)["today"]["reserved_calls"] == 1


def _reserve_in_process(database_url, gate, ready, outcomes):
    engine, factory = make_database(database_url)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        llm_api_key="process-fixture-key",
        llm_model="fixture",
        provider_daily_call_limit=1,
    )
    try:
        ready.put(True)
        if not gate.wait(10):
            outcomes.put("gate_timeout")
            return
        try:
            ProviderUsageStore(factory, settings).reserve("llm", "understand", now=NOW)
            outcomes.put("reserved")
        except ProviderLimitExceeded as exc:
            outcomes.put(exc.code)
    finally:
        engine.dispose()


def test_separate_processes_share_the_database_allowance(database, settings):
    context = multiprocessing.get_context("spawn")
    gate, ready, outcomes = context.Event(), context.Queue(), context.Queue()
    processes = [
        context.Process(
            target=_reserve_in_process, args=(settings.database_url, gate, ready, outcomes)
        )
        for _ in range(3)
    ]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=15)
        gate.set()
        results = [outcomes.get(timeout=15) for _ in processes]
        assert sorted(results) == ["daily_call_limit", "daily_call_limit", "reserved"]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        ready.close()
        outcomes.close()


@pytest.mark.asyncio
async def test_exhausted_budget_keeps_real_recommendation_on_rules(
    database, settings, item_factory
):
    settings = configured(settings, provider_daily_call_limit=1)
    with database.begin() as session:
        import_items(session, [item_factory()])
    store = ProviderUsageStore(database, settings)
    first = store.reserve("llm", "summary")
    store.finish(first, status="failed", latency_ms=1, error_code="timeout")
    calls = []

    def request(request):
        calls.append(request)
        raise AssertionError("An exhausted budget sent a provider request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as http:
        model = ModelClient(settings, http, usage_store=store)
        result = await recommend(database, settings, "帝国火炮", 3, model=model)
    assert result["engine"] == "rules" and result["status"] == "completed"
    assert result["items"] and calls == []
    assert any("降级" in warning for warning in result["warnings"])
    assert model.last_call["status"] == "blocked"
    assert model.last_call["error_code"] == "daily_call_limit"
    assert usage_report(database, settings)["today"]["reserved_calls"] == 1


@pytest.mark.asyncio
async def test_exhausted_shared_budget_preserves_bm25_fallback(database, settings, item_factory):
    settings = configured(
        settings,
        provider_daily_call_limit=1,
        llm_api_key="",
        llm_model="",
        retrieval_mode="hybrid",
        embedding_dimensions=2,
    )
    with database.begin() as session:
        import_items(session, [item_factory()])
        mod = session.get(Mod, "10001")
        session.add(
            ModEmbedding(
                mod_id=mod.id,
                namespace=embedding_namespace(settings),
                document_hash=document_fingerprint(mod),
                model=settings.embedding_model,
                dimensions=2,
                vector=[1, 0],
                created_at=utcnow(),
            )
        )
    store = ProviderUsageStore(database, settings)
    store.reserve("llm", "understand")
    calls = []

    def request(request):
        calls.append(request)
        raise AssertionError("An exhausted shared budget sent an embedding request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as http:
        embedding = EmbeddingClient(settings, http, usage_store=store)
        result = await recommend(database, settings, "帝国火炮", 3, embedding_client=embedding)
    assert result["retrieval"]["requested_method"] == "hybrid_rrf"
    assert result["retrieval"]["method"] == "bm25"
    assert result["items"] and calls == []
    assert any("退回 BM25" in warning for warning in result["warnings"])
    assert embedding.last_call["error_code"] == "daily_call_limit"
    assert usage_report(database, settings)["today"]["reserved_calls"] == 1
