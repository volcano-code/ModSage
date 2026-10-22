import asyncio
import json

import httpx
import pytest

from modsage.config import Settings
from modsage.embeddings import EmbeddingClient, EmbeddingUnavailable
from modsage.llm import ModelClient, ModelInvalidOutput, ModelUnavailable
from modsage.provider_config import provider_configuration, provider_fingerprint, valid_endpoint
from modsage.provider_usage import ProviderLimitExceeded


def llm_settings(settings, **changes):
    return settings.model_copy(
        update={
            "llm_api_key": "test-secret-key",
            "llm_base_url": "https://model.invalid/v1",
            "llm_model": "fixture-v1",
            **changes,
        }
    )


def embedding_settings(settings, **changes):
    return settings.model_copy(
        update={
            "embedding_api_key": "test-secret-key",
            "embedding_base_url": "https://model.invalid/v1",
            "embedding_model": "fixture-v1",
            **changes,
        }
    )


def completion(**changes):
    return {
        "model": "fixture-v1",
        "choices": [
            {"message": {"content": '{"keywords":["artillery"]}'}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        **changes,
    }


class Recorder:
    def __init__(self, reserve_error=None, finish_error=None, finish_result=True):
        self.reserve_error = reserve_error
        self.finish_error = finish_error
        self.finish_result = finish_result
        self.reservations = []
        self.outcomes = []

    def reserve(self, **fields):
        if self.reserve_error:
            raise ProviderLimitExceeded(self.reserve_error)
        self.reservations.append(fields)
        return "test-call"

    def finish(self, call_id, **fields):
        self.outcomes.append({"call_id": call_id, **fields})
        if self.finish_error:
            raise ProviderLimitExceeded(self.finish_error)
        return self.finish_result


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://",
        "file:///tmp/key",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/v1?",
        "https://example.com/v1#",
        "https://example.com:bad/v1",
        "https://example.com:65536/v1",
        "https://example.com:0/v1",
        "https://example.com:/v1",
        "https://exa mple.com/v1",
        "https://example.com/\npath",
        "https://example.com/v1\\path",
        "https://example.com/v1/chat/completions/",
        "https://example.com/v1/embeddings",
        "http://[bad-ip]/v1",
        "https://-example.com/v1",
        "https://example.com/%0Apath",
        "https://example.com/v1/%63hat/completions",
    ],
)
def test_endpoint_validation_rejects_unsafe_or_completed_operation_urls(value):
    assert valid_endpoint(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "https://api.example.com/v1",
        "http://localhost:11434/v1",
        "http://[::1]:8000/v1",
        "http://model-service:8080/api/v2",
    ],
)
def test_endpoint_validation_allows_explicit_compatible_local_services(value):
    assert valid_endpoint(value) is True


def test_configuration_presence_is_not_availability_and_blank_base_never_passes(settings):
    assert provider_configuration(settings, "llm")["state"] == "not_configured"
    incomplete = llm_settings(settings, llm_base_url="")
    status = provider_configuration(incomplete, "llm")
    assert incomplete.llm_configured is False
    assert status == {
        "state": "incomplete",
        "configured": False,
        "missing_fields": ["base_url"],
        "issues": [],
    }
    invalid = llm_settings(settings, llm_base_url="https://user:secret@example.com/v1")
    assert provider_configuration(invalid, "llm")["state"] == "invalid"
    assert "secret" not in json.dumps(provider_configuration(invalid, "llm"))


def test_configuration_normalization_and_empty_temperature():
    settings = Settings(
        _env_file=None,
        llm_api_key="  local-test-key  ",
        llm_base_url=" http://localhost:11434/v1/ ",
        llm_model=" fixture ",
        llm_temperature="",
        embedding_api_key=" embedding-test-key ",
        embedding_base_url=" https://embedding.invalid/v1 ",
        embedding_model=" alias ",
        embedding_response_model=" canonical-v1 ",
    )
    assert settings.llm_api_key == "local-test-key"
    assert settings.llm_model == "fixture"
    assert settings.llm_temperature is None
    assert settings.llm_configured and settings.embedding_configured
    assert "local-test-key" not in repr(settings)
    assert "embedding-test-key" not in repr(settings)
    assert settings.embedding_response_model == "canonical-v1"


def test_provider_verification_fingerprint_changes_with_key_and_wire_contract(settings):
    setting = llm_settings(settings)
    identity = provider_fingerprint(setting, "llm")
    for changes in [
        {"llm_api_key": "rotated"},
        {"llm_base_url": "https://other.invalid/v1"},
        {"llm_model": "another"},
        {"llm_token_parameter": "max_completion_tokens"},
        {"llm_temperature": None},
        {"llm_max_output_tokens": 1500},
    ]:
        assert provider_fingerprint(setting.model_copy(update=changes), "llm") != identity
    assert len(identity) == 64 and "test-secret" not in identity


async def test_compatible_llm_request_records_safe_usage_and_optional_parameters(settings):
    recorder = Recorder()

    def handle(request):
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 1200
        assert "max_tokens" not in body and "temperature" not in body
        return httpx.Response(200, json=completion(), headers={"x-request-id": "req_fixture"})

    setting = llm_settings(
        settings,
        llm_token_parameter="max_completion_tokens",
        llm_max_output_tokens=1200,
        llm_temperature=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        model = ModelClient(setting, http, usage_store=recorder, operation="canary_understand")
        assert await model.json_response("test", {"query": "火炮"}) == {"keywords": ["artillery"]}
    assert recorder.reservations == [{"provider": "llm", "operation": "canary_understand"}]
    assert len(recorder.outcomes) == 1
    result = recorder.outcomes[0]
    assert result["status"] == "succeeded" and result["total_tokens"] == 28
    assert result["input_tokens"] == 20 and result["output_tokens"] == 8
    assert result["request_id"] == "req_fixture" and result["finish_reason"] == "stop"
    assert result["latency_ms"] >= 0 and model.last_call["call_id"] == "test-call"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (429, "rate_limited"),
        (503, "http_error"),
        (302, "http_error"),
    ],
)
async def test_provider_status_errors_are_classified_without_following_redirects(
    settings, status, code
):
    recorder = Recorder()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            text="test-secret-key original-user-text",
            headers={"location": "https://elsewhere.invalid"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        model = ModelClient(llm_settings(settings), http, usage_store=recorder)
        with pytest.raises(ModelUnavailable) as error:
            await model.json_response("test", {})
    assert error.value.code == code and len(requests) == 1
    assert recorder.outcomes[0]["status"] == "failed"
    assert recorder.outcomes[0]["error_code"] == code
    assert "test-secret-key" not in json.dumps(model.last_call) + str(error.value)
    assert "original-user-text" not in json.dumps(model.last_call) + str(error.value)


@pytest.mark.parametrize(
    "payload,code",
    [
        (
            completion(choices=[{"message": {"content": "{}"}, "finish_reason": "length"}]),
            "output_truncated",
        ),
        (
            completion(choices=[{"message": {"content": "{}"}, "finish_reason": "content_filter"}]),
            "content_filtered",
        ),
        (
            completion(choices=[{"message": {"content": "{}"}, "finish_reason": {}}]),
            "invalid_response",
        ),
        (
            completion(choices=[{"message": {"content": "{}"}, "finish_reason": "error"}]),
            "invalid_response",
        ),
        (completion(choices=[1]), "invalid_response"),
        (
            completion(choices=[{"message": {"content": r'{"keywords":["\ud800"]}'}}]),
            "invalid_response",
        ),
        (completion(choices=[{"message": {"content": "not JSON"}}]), "invalid_json"),
        (completion(choices=[{"message": {"content": '{"value":NaN}'}}]), "invalid_json"),
        (completion(usage={"prompt_tokens": -1}), "invalid_usage"),
        (completion(usage={"total_tokens": True}), "invalid_usage"),
        (
            completion(usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 21}),
            "invalid_usage",
        ),
        (completion(usage="upstream secret response"), "invalid_usage"),
    ],
)
async def test_http_200_with_invalid_model_output_never_records_success(settings, payload, code):
    recorder = Recorder()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http:
        model = ModelClient(llm_settings(settings), http, usage_store=recorder)
        with pytest.raises((ModelInvalidOutput, ModelUnavailable)) as error:
            await model.json_response("test", {})
    assert error.value.code == code
    assert recorder.outcomes[0]["status"] == "failed"
    assert recorder.outcomes[0]["error_code"] == code


async def test_missing_usage_stays_unknown_and_provider_metadata_cannot_echo_keys(settings):
    payload = completion(model="test-secret-key", usage=None)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload, headers={"x-request-id": "test-secret-key"})
        )
    ) as http:
        model = ModelClient(llm_settings(settings), http)
        await model.json_response("test", {})
    assert model.last_call["status"] == "succeeded"
    assert model.last_call["total_tokens"] is None and model.last_call["input_tokens"] is None
    assert model.last_call["request_id"] is None and model.last_call["returned_model"] is None


@pytest.mark.parametrize(
    "code",
    ["provider_calls_disabled", "daily_call_limit", "minute_call_limit", "usage_unavailable"],
)
async def test_quota_failure_prevents_http_request(settings, code):
    recorder = Recorder(reserve_error=code)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as http:
        model = ModelClient(llm_settings(settings), http, usage_store=recorder)
        with pytest.raises(ModelUnavailable) as error:
            await model.json_response("test", {})
    assert error.value.code == code
    assert not calls and not recorder.outcomes
    assert model.last_call["status"] == "blocked"


@pytest.mark.parametrize(
    "options", [{"finish_error": "usage_unavailable"}, {"finish_result": False}]
)
async def test_ledger_finish_failure_does_not_report_provider_success(settings, options):
    recorder = Recorder(**options)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion()))
    ) as http:
        model = ModelClient(llm_settings(settings), http, usage_store=recorder)
        with pytest.raises(ModelUnavailable) as error:
            await model.json_response("test", {})
    assert error.value.code == "usage_unavailable"
    assert len(recorder.outcomes) == 1
    assert model.last_call["status"] == "failed"


async def test_cancelled_inflight_call_is_recorded_once_and_cancellation_propagates(settings):
    recorder = Recorder()
    entered = asyncio.Event()

    async def handle(_):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        model = ModelClient(llm_settings(settings), http, usage_store=recorder)
        task = asyncio.create_task(model.json_response("test", {}))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(recorder.outcomes) == 1
    assert recorder.outcomes[0]["status"] == "cancelled"
    assert recorder.outcomes[0]["error_code"] == "cancelled"


async def test_total_timeout_covers_slow_stream_and_preserves_usage_reservation(settings):
    recorder = Recorder()

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"choices":'
            await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowBody()))
    ) as http:
        model = ModelClient(
            llm_settings(settings, llm_timeout_seconds=0.02), http, usage_store=recorder
        )
        with pytest.raises(ModelUnavailable) as error:
            await model.json_response("test", {})
    assert error.value.code == "timeout"
    assert len(recorder.reservations) == len(recorder.outcomes) == 1
    assert recorder.outcomes[0]["status"] == "failed"


async def test_response_size_is_checked_before_json_loading(settings, monkeypatch):
    monkeypatch.setattr("modsage.llm.MAX_RESPONSE_BYTES", 12)
    recorder = Recorder()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 13))
    ) as http:
        model = ModelClient(llm_settings(settings), http, usage_store=recorder)
        with pytest.raises(ModelUnavailable) as error:
            await model.json_response("test", {})
    assert error.value.code == "response_too_large"


async def test_default_clients_require_durable_admission_before_real_http(settings):
    model = ModelClient(llm_settings(settings))
    with pytest.raises(ModelUnavailable) as error:
        await model.json_response("test", {})
    assert error.value.code == "usage_unavailable"
    assert model.last_call["status"] == "blocked"
    provider = EmbeddingClient(embedding_settings(settings))
    with pytest.raises(EmbeddingUnavailable) as error:
        await provider.embed(["source"])
    assert error.value.code == "usage_unavailable"
    assert provider.last_call["status"] == "blocked"


async def test_embedding_alias_is_opt_in_and_usage_is_measured(settings):
    payload = {
        "model": "canonical-v1",
        "data": [{"index": 0, "embedding": [3, 4]}],
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }
    recorder = Recorder()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http:
        provider = EmbeddingClient(embedding_settings(settings), http)
        with pytest.raises(EmbeddingUnavailable) as error:
            await provider.embed(["source"])
        assert error.value.code == "unexpected_model"
        provider = EmbeddingClient(
            embedding_settings(settings, embedding_response_model="canonical-v1"),
            http,
            usage_store=recorder,
        )
        assert await provider.embed(["source"]) == [[0.6, 0.8]]
    assert provider.last_call["status"] == "succeeded"
    assert provider.last_call["total_tokens"] == 3
    assert provider.last_call["output_tokens"] is None
    assert recorder.outcomes[0]["returned_model"] == "canonical-v1"
