import json

import httpx
import pytest

from modsage.config import Settings
from modsage.embeddings import (
    EmbeddingClient,
    EmbeddingUnavailable,
    backfill_embeddings,
    embedding_namespace,
    embedding_status,
)
from modsage.ingestion import import_items


def configured(settings, **options):
    return settings.model_copy(
        update={
            "embedding_api_key": "test-only",
            "embedding_base_url": "https://embedding.example/v1",
            "embedding_model": "fixture-v1",
            **options,
        }
    )


def test_empty_compose_dimension_and_optional_provider_defaults():
    settings = Settings(
        _env_file=None,
        embedding_dimensions="",
        embedding_api_key="",
        embedding_model="",
        embedding_base_url="",
    )
    assert settings.embedding_dimensions is None
    assert settings.embedding_configured is False
    assert settings.retrieval_mode == "bm25"


def test_namespace_changes_for_model_endpoint_dimension_but_not_credentials(settings):
    original = configured(settings)
    namespace = embedding_namespace(original)
    assert namespace == embedding_namespace(
        original.model_copy(update={"embedding_api_key": "rotated"})
    )
    for patch in [
        {"embedding_model": "v2"},
        {"embedding_base_url": "https://another.example/v1"},
        {"embedding_dimensions": 128},
    ]:
        assert namespace != embedding_namespace(original.model_copy(update=patch))


@pytest.mark.asyncio
async def test_provider_checks_order_and_normalizes_vectors(settings):
    setting = configured(settings, embedding_dimensions=2)

    async def handler(request):
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-only"
        payload = json.loads(request.content)
        assert payload == {
            "model": "fixture-v1",
            "input": ["first", "second"],
            "encoding_format": "float",
            "dimensions": 2,
        }
        return httpx.Response(
            200,
            json={
                "model": "fixture-v1",
                "data": [
                    {"index": 1, "embedding": [0, 10]},
                    {"index": 0, "embedding": [3, 4]},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await EmbeddingClient(setting, http).embed(["first", "second"])
    assert result == [[0.6, 0.8], [0, 1]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"index": 0, "embedding": [0, 0]}]},
        {"data": [{"index": 0, "embedding": [1, 0, 0]}]},
        {"data": [{"index": 0, "embedding": [True, 1]}]},
        {"data": [{"index": 1, "embedding": [1, 0]}]},
        {"data": [{"index": False, "embedding": [1, 0]}]},
        {"data": []},
        {"model": "wrong-provider-model", "data": [{"index": 0, "embedding": [1, 0]}]},
        {"data": [{"embedding": [1, 0]}]},
    ],
)
async def test_provider_invalid_shape_is_a_safe_error(settings, payload):
    setting = configured(settings, embedding_dimensions=2)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(EmbeddingUnavailable, match="invalid vectors"):
            await EmbeddingClient(setting, http).embed(["source"])


@pytest.mark.asyncio
async def test_duplicate_embedding_index_is_rejected(settings):
    payload = {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [0, 1]}]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(EmbeddingUnavailable):
            await EmbeddingClient(configured(settings), http).embed(["first", "second"])


@pytest.mark.asyncio
async def test_provider_http_error_does_not_expose_source_or_credentials(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="private-key sensitive-source")
        )
    ) as http:
        with pytest.raises(EmbeddingUnavailable) as failure:
            await EmbeddingClient(configured(settings), http).embed(["source"])
    assert "private-key" not in str(failure.value)
    assert "sensitive-source" not in str(failure.value)


@pytest.mark.asyncio
async def test_provider_response_size_and_request_bounds(settings, monkeypatch):
    monkeypatch.setattr("modsage.embeddings.MAX_RESPONSE_BYTES", 10)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 11))
    ) as http:
        client = EmbeddingClient(configured(settings), http)
        with pytest.raises(EmbeddingUnavailable, match="exceeded"):
            await client.embed(["source"])
        with pytest.raises(EmbeddingUnavailable, match="input"):
            await client.embed(["x"] * 17)
        with pytest.raises(EmbeddingUnavailable, match="input"):
            await client.embed(["x" * 6001])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/local",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?key=secret",
        "http://[invalid",
        "https://example.com:bad/v1",
        "https://example.com:99999/v1",
    ],
)
async def test_invalid_endpoint_is_rejected_before_network(settings, endpoint):
    with pytest.raises(EmbeddingUnavailable, match="endpoint"):
        await EmbeddingClient(configured(settings, embedding_base_url=endpoint)).embed(["source"])


@pytest.mark.asyncio
async def test_unconfigured_backfill_is_explicit_and_does_not_use_chat_key(database, settings):
    setting = settings.model_copy(update={"llm_api_key": "chat-key", "llm_model": "chat-model"})
    with pytest.raises(EmbeddingUnavailable, match="not configured"):
        await backfill_embeddings(database, setting)
    for args in [{"limit": 201}, {"batch_size": 17}, {"limit": 0}]:
        with pytest.raises(ValueError):
            await backfill_embeddings(database, setting, **args)


@pytest.mark.asyncio
async def test_index_status_is_namespace_specific(database, settings, item_factory):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Provider:
        async def embed(self, texts):
            return [[1, 0] for _ in texts]

    await backfill_embeddings(database, setting, client=Provider())
    assert embedding_status(database, setting)["indexed"] == 1
    changed = configured(settings, embedding_model="different-model")
    status = embedding_status(database, changed)
    assert status["indexed"] == 0
    assert status["missing"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("vectors", [[], [[0, 0]], [[1, 0], [0, 1]]])
async def test_backfill_does_not_commit_invalid_provider_batch(
    database, settings, item_factory, vectors
):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Provider:
        async def embed(self, texts):
            return vectors

    result = await backfill_embeddings(database, setting, client=Provider())
    assert result["status"] == "failed"
    assert result["generated"] == 0
    assert embedding_status(database, setting)["indexed"] == 0


@pytest.mark.asyncio
async def test_embedding_cli_status_and_missing_configuration(database, settings):
    from types import SimpleNamespace

    from modsage.cli import execute

    status = await execute(SimpleNamespace(command="embedding-status"), settings, database)
    assert status["configured"] is False
    assert status["indexed"] == 0
    assert "embedding_api_key" not in status
    with pytest.raises(EmbeddingUnavailable):
        await execute(SimpleNamespace(command="embed", limit=1, batch_size=1), settings, database)
