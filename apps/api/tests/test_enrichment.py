import httpx
import pytest

from modsage.enrichment import enrich_mods
from modsage.ingestion import import_items
from modsage.llm import ModelClient, ModelUnavailable
from modsage.models import Mod


@pytest.mark.asyncio
async def test_unconfigured_model_fails_without_inventing_translation(database, settings):
    with pytest.raises(ModelUnavailable):
        await enrich_mods(database, settings)


@pytest.mark.asyncio
async def test_summary_requires_literal_evidence(database, settings, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Model:
        async def json_response(self, system, data):
            return {
                "summary_zh": "这个测试模组增加帝国火炮兵种，具体功能以作者原文为准。",
                "evidence_quotes": ["Fabricated source claim that does not exist."],
            }

    configured = settings.model_copy(update={"llm_api_key": "test-only", "llm_model": "fixture"})
    result = await enrich_mods(database, configured, model=Model())
    assert result["generated"] == 0
    assert result["errors"]
    with database() as session:
        assert session.get(Mod, "10001").summary_status == "pending"


@pytest.mark.asyncio
async def test_valid_summary_does_not_upgrade_compatibility_or_language(
    database, settings, item_factory
):
    item = item_factory()
    with database.begin() as session:
        import_items(session, [item])

    class Model:
        async def json_response(self, system, data):
            return {
                "summary_zh": "这个测试模组为帝国增加火炮兵种，介绍依据作者提供的描述。",
                "evidence_quotes": ["Adds artillery units for the Empire."],
            }

    configured = settings.model_copy(update={"llm_api_key": "test-only", "llm_model": "fixture"})
    result = await enrich_mods(database, configured, model=Model())
    assert result["generated"] == 1
    with database.begin() as session:
        import_items(session, [item])
        mod = session.get(Mod, "10001")
        assert mod.chinese_support == "unknown"
        assert mod.compatibility_status == "unknown"
        assert mod.summary_status == "generated"
        assert any(e["field"] == "summary_zh" for e in mod.evidence)


@pytest.mark.asyncio
async def test_model_transport_uses_bounded_json_request(settings):
    configured = settings.model_copy(
        update={
            "llm_api_key": "test-only",
            "llm_model": "fixture",
            "llm_base_url": "https://model.invalid/v1",
        }
    )

    def handle(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"keywords": ["artillery"]}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await ModelClient(configured, client=client).json_response(
            "test", {"query": "火炮"}
        )
    assert result["keywords"] == ["artillery"]


@pytest.mark.asyncio
async def test_model_errors_never_echo_provider_body(settings):
    configured = settings.model_copy(update={"llm_api_key": "test-secret", "llm_model": "fixture"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="test-secret"))
    ) as client:
        with pytest.raises(ModelUnavailable) as error:
            await ModelClient(configured, client=client).json_response("test", {})
        assert "test-secret" not in str(error.value)
