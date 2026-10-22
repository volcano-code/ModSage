import json

import httpx
import pytest
from sqlalchemy import func, select

from modsage import cli
from modsage.config import Settings
from modsage.ingestion import import_items
from modsage.models import Mod, ModEmbedding, ProviderCall, RecommendationJob, RecommendationRun
from modsage.provider_checks import preflight, provider_check


def configured(settings, **updates):
    return settings.model_copy(
        update={
            "llm_api_key": "test-private-key",
            "llm_base_url": "https://chat.example/v1",
            "llm_model": "test-chat-model",
            "embedding_api_key": "test-embedding-key",
            "embedding_base_url": "https://vectors.example/v1",
            "embedding_model": "test-vector-model",
            "embedding_dimensions": 2,
            "steam_api_key": "test-steam-key",
            **updates,
        }
    )


@pytest.fixture
def source_sample(database, item_factory):
    item = item_factory()
    with database.begin() as session:
        import_items(session, [item])
    return item


def llm_response(request, *, bad_constraints=False, bad_quotes=False, usage=True):
    data = json.loads(json.loads(request.content)["messages"][-1]["content"])
    if "source_description" in data:
        result = {
            "summary_zh": "该模组为帝国派系增加火炮兵种，具体兵种与使用条件请以作者说明为准。",
            "evidence_quotes": [
                "This quote is not in the source."
                if bad_quotes
                else data["source_description"][:40]
            ],
        }
    else:
        result = {"invalid_schema": True} if bad_constraints else {"factions": ["empire"]}
    body = {
        "model": "test-chat-model",
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return httpx.Response(200, json=body, headers={"x-request-id": "fixture-request"})


def vectors_response(request, *, invalid=False, malformed_usage=False):
    data = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "model": "test-vector-model",
            "data": [
                {"index": index, "embedding": [0, 0] if invalid else [1, 0]}
                for index, _ in enumerate(data["input"])
            ],
            "usage": {"prompt_tokens": 10, "total_tokens": -1 if malformed_usage else 10},
        },
    )


def test_preflight_is_offline_and_does_not_call_unconfigured_services(
    database, settings, monkeypatch
):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("offline preflight must not construct an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", network_forbidden)
    result = preflight(database, settings)
    assert result["status"] == "ready"
    assert result["network_calls"] == 0
    assert result["live_verified"] is False
    assert result["catalog"]["total"] == 0
    assert result["providers"]["llm"]["state"] == "not_configured"
    assert "catalog_empty" in result["warnings"]


def test_preflight_reports_configuration_and_timeout_conflicts_without_secrets(database, settings):
    setting = configured(
        settings,
        llm_base_url="https://private-user:test-private-key@example.com/v1?key=secret",
        steam_api_key="",
        refresh_enabled=True,
        refresh_enrich_limit=1,
        retrieval_mode="hybrid",
        recommendation_timeout_seconds=5,
    )
    result = preflight(database, setting)
    assert result["status"] == "failed"
    assert result["providers"]["llm"]["state"] == "invalid"
    assert (
        "recommendation_timeout_below_provider_timeouts_plus_processing_margin"
        in result["warnings"]
    )
    assert "hybrid_will_fall_back_without_current_vectors" in result["warnings"]
    assert {"scope": "refresh", "code": "scheduled_steam_discovery_not_configured"} in result[
        "errors"
    ]
    serialized = json.dumps(result)
    assert "test-private-key" not in serialized
    assert "private-user" not in serialized
    assert "example.com" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options,code",
    [
        ({}, "live_flag_required"),
        ({"live": True, "scope": "bad"}, "invalid_scope"),
        ({"live": True, "query": "x"}, "invalid_query"),
        ({"live": True, "mod_id": "0"}, "invalid_mod_id"),
    ],
)
async def test_canary_requires_explicit_live_and_valid_input(database, settings, options, code):
    result = await provider_check(database, configured(settings), **options)
    assert result["status"] == "blocked"
    assert any(error["code"] == code for error in result["errors"])
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ProviderCall)) == 0


@pytest.mark.asyncio
async def test_all_scope_checks_every_prerequisite_before_spending(
    database, settings, source_sample
):
    attempts = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: attempts.append(request))
    ) as http:
        result = await provider_check(
            database,
            configured(settings, steam_api_key=""),
            scope="all",
            live=True,
            model_http=http,
            embedding_http=http,
        )
    assert result["status"] == "blocked"
    assert result["errors"] == [{"scope": "steam", "code": "configuration_not_configured"}]
    assert attempts == []


@pytest.mark.asyncio
async def test_empty_source_catalog_is_not_live_success(database, settings):
    result = await provider_check(database, configured(settings), live=True)
    assert result["status"] == "blocked"
    assert any(
        error["code"] == "no_catalog_sample_with_source_description" for error in result["errors"]
    )
    assert result["effects"]["usage_records_written"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates,code",
    [
        ({"provider_calls_enabled": False}, "provider_calls_disabled"),
        ({"provider_daily_call_limit": 1}, "insufficient_canary_call_budget"),
        ({"provider_minute_call_limit": 1}, "insufficient_canary_call_budget"),
    ],
)
async def test_canary_prechecks_the_full_call_budget(
    database, settings, source_sample, updates, code
):
    result = await provider_check(database, configured(settings, **updates), live=True)
    assert result["status"] == "blocked"
    assert any(error["code"] == code for error in result["errors"])


@pytest.mark.asyncio
async def test_llm_canary_runs_real_graph_and_summary_without_changing_catalog(
    database, settings, source_sample
):
    requests = []

    def handle(request):
        requests.append(request)
        return llm_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await provider_check(database, configured(settings), live=True, model_http=http)
    assert result["status"] == "succeeded", result
    assert len(requests) == 2
    assert [check["name"] for check in result["checks"]] == ["llm_recommendation", "llm_summary"]
    assert result["checks"][0]["engine"] == "llm"
    assert result["checks"][1]["manual_review_required"] is True
    assert result["checks"][1]["semantic_faithfulness_verified"] is False
    assert result["usage"] == {
        "recorded_attempts": 2,
        "status": "complete",
        "currency_cost_verified": False,
        "known_total_tokens": 30,
        "calls_without_total_tokens": 0,
    }
    with database() as session:
        assert session.get(Mod, source_sample.id).summary_status == "pending"
        assert session.get(Mod, source_sample.id).summary_zh is None
        assert session.scalar(select(func.count()).select_from(ProviderCall)) == 2
        for model in (ModEmbedding, RecommendationRun, RecommendationJob):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert "test-private-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_rules_fallback_cannot_pass_canary_even_when_recommendations_complete(
    database, settings, source_sample
):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(401, text="test-private-key source details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await provider_check(database, configured(settings), live=True, model_http=http)
    assert result["status"] == "failed"
    assert result["checks"][0]["engine"] == "rules"
    assert result["checks"][0]["item_count"] == 1
    assert result["checks"][0]["error_code"] == "unauthorized"
    assert result["checks"][1]["status"] == "blocked"
    assert len(requests) == 1
    assert "test-private-key" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_constraints,bad_quotes", [(True, False), (False, True)])
async def test_valid_json_without_valid_business_output_is_not_success(
    database, settings, source_sample, bad_constraints, bad_quotes
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: llm_response(
                request, bad_constraints=bad_constraints, bad_quotes=bad_quotes
            )
        )
    ) as http:
        result = await provider_check(database, configured(settings), live=True, model_http=http)
    assert result["status"] == "failed"
    failed = next(check for check in result["checks"] if check["status"] == "failed")
    assert (
        failed["call"]["status"] == "succeeded"
    )  # JSON transfer is distinct from business validity.
    with database() as session:
        assert session.get(Mod, source_sample.id).summary_zh is None


@pytest.mark.asyncio
async def test_missing_usage_stays_incomplete_not_zero(database, settings, source_sample):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: llm_response(request, usage=False))
    ) as http:
        result = await provider_check(database, configured(settings), live=True, model_http=http)
    assert result["status"] == "succeeded"
    assert result["usage"]["status"] == "incomplete"
    assert result["usage"]["calls_without_total_tokens"] == 2
    assert result["checks"][0]["call"]["total_tokens"] is None
    assert result["warnings"] == ["provider_usage_missing_not_zero"]


@pytest.mark.asyncio
async def test_embedding_canary_uses_temporary_backfill_and_real_hybrid_query(
    database, settings, source_sample
):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/v1/embeddings"
        return vectors_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await provider_check(
            database, configured(settings), scope="embedding", live=True, embedding_http=http
        )
    assert result["status"] == "succeeded", result
    assert len(requests) == 2
    assert result["checks"][0]["generated"] == 1
    assert result["checks"][1]["retrieval_method"] == "hybrid_rrf"
    assert result["checks"][1]["quality_benchmark_passed"] is False
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ModEmbedding)) == 0
        assert session.scalar(select(func.count()).select_from(ProviderCall)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid,malformed_usage", [(True, False), (False, True)])
async def test_invalid_vectors_or_usage_never_pass(
    database, settings, source_sample, invalid, malformed_usage
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: vectors_response(
                request, invalid=invalid, malformed_usage=malformed_usage
            )
        )
    ) as http:
        result = await provider_check(
            database, configured(settings), scope="embedding", live=True, embedding_http=http
        )
    assert result["status"] == "failed"
    assert result["checks"][0]["generated"] == 0
    assert result["checks"][1]["status"] == "blocked"
    if malformed_usage:
        assert result["checks"][0]["error_code"] == "invalid_usage"


@pytest.mark.asyncio
async def test_bm25_fallback_after_indexing_does_not_count_as_hybrid_success(
    database, settings, source_sample
):
    async with httpx.AsyncClient(transport=httpx.MockTransport(vectors_response)) as http:
        result = await provider_check(
            database,
            configured(settings),
            scope="embedding",
            live=True,
            query="只推荐基斯里夫的新兵种",
            embedding_http=http,
        )
    assert result["checks"][0]["status"] == "succeeded"
    assert result["status"] == "failed"
    assert result["checks"][1]["retrieval_method"] == "bm25"
    assert result["checks"][1]["error_code"] == "hybrid_not_exercised"


def steam_response(request, *, empty=False, wrong_game=False, invalid_first_page=False):
    if "QueryFiles" in request.url.path:
        assert request.headers["x-webapi-key"] == "test-steam-key"
        assert "test-steam-key" not in str(request.url)
        items = [] if empty else [{"publishedfileid": "invalid" if invalid_first_page else "10001"}]
        return httpx.Response(
            200, json={"response": {"publishedfiledetails": items, "next_cursor": "next"}}
        )
    return httpx.Response(
        200,
        json={
            "response": {
                "publishedfiledetails": [
                    {
                        "publishedfileid": "10001",
                        "result": 1,
                        "consumer_app_id": 1 if wrong_game else 1142710,
                        "title": "Fixture Mod",
                        "description": "Explicit fixture source description",
                        "file_type": 0,
                    }
                ]
            }
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options,status,expected_calls",
    [
        ({}, "succeeded", 2),
        ({"empty": True}, "failed", 1),
        ({"wrong_game": True}, "failed", 2),
        ({"invalid_first_page": True}, "failed", 1),
    ],
)
async def test_steam_canary_requires_actual_wh3_details_and_is_one_page_bounded(
    database, settings, options, status, expected_calls
):
    requests = []

    def handle(request):
        requests.append(request)
        return steam_response(request, **options)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await provider_check(
            database, configured(settings), scope="steam", live=True, steam_http=http
        )
    assert result["status"] == status
    assert len(requests) == expected_calls
    assert result["budget"]["max_steam_http_attempts"] == 6
    assert result["usage"]["status"] == "not_applicable"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Mod)) == 0
    assert "test-steam-key" not in json.dumps(result)


def test_cli_preflight_and_usage_are_offline_and_provider_check_requires_flag(
    settings, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    for command, exit_code in [("preflight", 0), ("usage", 0), ("provider-check", 1)]:
        monkeypatch.setattr("sys.argv", ["modsage", command])
        assert cli.main() == exit_code
        result = json.loads(capsys.readouterr().out)
        if command == "provider-check":
            assert result["status"] == "blocked"
            assert result["errors"][0]["code"] == "live_flag_required"


def test_cli_configuration_failure_does_not_print_input_value(monkeypatch, capsys):
    def bad_settings():
        return Settings(_env_file=None, provider_daily_call_limit="private-value-do-not-print")

    monkeypatch.setattr(cli, "get_settings", bad_settings)
    monkeypatch.setattr("sys.argv", ["modsage", "preflight"])
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert "private-value-do-not-print" not in output
    assert json.loads(output)["error_type"] == "ValidationError"


def test_cli_database_startup_failure_has_no_url_or_credentials(settings, monkeypatch, capsys):
    setting = settings.model_copy(
        update={"database_url": "unknown://user:private-password@localhost/db"}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: setting)
    monkeypatch.setattr("sys.argv", ["modsage", "preflight"])
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert "private-password" not in output
    assert "unknown://" not in output
