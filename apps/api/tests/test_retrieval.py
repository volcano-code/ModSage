import asyncio
from types import SimpleNamespace

import pytest

from modsage.embeddings import (
    EmbeddingUnavailable,
    backfill_embeddings,
    document_fingerprint,
    embedding_namespace,
)
from modsage.ingestion import import_items
from modsage.models import Mod, ModEmbedding, RecommendationRun
from modsage.recommendations import recommend
from modsage.retrieval import (
    RankedCandidate,
    fuse_rankings,
    normalized_vector,
    rank_bm25,
    rank_semantic,
    tokenize,
)


def mod(item_id, title, description="", tags=()):
    return SimpleNamespace(id=item_id, title=title, description=description, tags=list(tags))


def configured(settings, **options):
    return settings.model_copy(
        update={
            "retrieval_mode": "hybrid",
            "embedding_api_key": "test-only",
            "embedding_base_url": "https://embeddings.example/v1",
            "embedding_model": "fixture-v1",
            **options,
        }
    )


def index(session, settings, record, vector=(1.0, 0.0)):
    session.add(
        ModEmbedding(
            mod_id=record.id,
            namespace=embedding_namespace(settings),
            document_hash=document_fingerprint(record),
            model=settings.embedding_model,
            dimensions=len(vector),
            vector=list(vector),
        )
    )


class FixedProvider:
    def __init__(self, vector=(1.0, 0.0)):
        self.vector = list(vector)
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        return [self.vector[:] for _ in texts]


def test_bilingual_tokenizer_has_normalization_bigrams_and_domain_aliases():
    assert "empire" in tokenize("Ｅｍｐｉｒｅ units")
    assert "unit" in tokenize("Units")
    assert "外交" in tokenize("改善外交关系")
    assert "faction:empire" in tokenize("帝国火炮")
    assert "faction:empire" in tokenize("Imperial artillery")
    assert "want" not in tokenize("I want some mods")


def test_bm25_prefers_title_evidence_and_limits_cross_promotion():
    records = [
        mod("a", "Diplomacy Rebalance", "Balances negotiations"),
        mod("b", "Campaign improvements", "Changes diplomacy and other campaign elements"),
        mod("c", "Textures", "New skin.\nOther mods: Diplomacy Rebalance"),
    ]
    ranked = rank_bm25(records, "diplomacy")
    assert [item.id for item in ranked] == ["a", "b"]
    assert ranked[0].matched_terms == ["diplomacy"]
    assert ranked[0].channels == ["lexical"]


def test_bm25_retrieves_english_from_chinese_and_partial_chinese_phrases():
    records = [mod("a", "Empire Artillery", "Adds cannons"), mod("b", "外交关系调整")]
    assert rank_bm25(records, "帝国火炮")[0].id == "a"
    assert rank_bm25(records, "希望改善外交关系")[0].id == "b"
    assert rank_bm25(records, "totally absent keyword") == []
    assert rank_bm25(records, "some mods please") == []


def test_bm25_normalizes_document_length_and_breaks_ties_stably():
    records = [
        mod("b", "Shared", "diplomacy " + "irrelevant " * 120),
        mod("a", "Shared", "diplomacy"),
        mod("c", "Shared", "diplomacy"),
    ]
    ranked = rank_bm25(records, "diplomacy")
    assert [item.id for item in ranked] == ["a", "c", "b"]
    assert ranked[0].score > ranked[-1].score


def test_chinese_summary_is_searchable_only_when_catalog_explicitly_requests_it():
    record = mod("a", "Treaty Adjustments", "Changes diplomacy and negotiations.", ["Campaign"])
    record.summary_zh = "调整盟友的外交关系。"
    assert rank_bm25([record], "外交") == []
    assert rank_bm25([record], "外交", include_summary=True)[0].id == "a"


def test_rrf_combines_ranks_without_comparing_unrelated_score_scales():
    a, b, c = mod("a", "A"), mod("b", "B"), mod("c", "C")
    lexical = [RankedCandidate(a, 1000, lexical_score=1000), RankedCandidate(b, 2, lexical_score=2)]
    semantic = [
        RankedCandidate(b, 0.9, semantic_score=0.9),
        RankedCandidate(c, 0.8, semantic_score=0.8),
    ]
    fused = fuse_rankings(lexical, semantic)
    assert [item.id for item in fused] == ["b", "a", "c"]
    assert fused[0].channels == ["lexical", "semantic"]
    assert fused[0].lexical_score == 2
    assert fused[0].semantic_score == 0.9
    assert len(fuse_rankings(lexical, semantic, limit=1)) == 1


@pytest.mark.parametrize(
    "vector", [[], [0, 0], [float("nan")], [float("inf")], [True, 1], ["1"], [1] * 4097]
)
def test_invalid_vectors_never_reach_similarity(vector):
    assert normalized_vector(vector) is None
    assert rank_semantic([mod("a", "A")], [1, 0], {"a": vector}) == []


def test_cosine_does_not_cross_dimension_or_minimum_boundaries():
    records = [mod("a", "A"), mod("b", "B"), mod("c", "C")]
    ranked = rank_semantic(records, [1, 0], {"a": [10, 0], "b": [0, 10], "c": [1, 0, 0]})
    assert [item.id for item in ranked] == ["a"]
    assert ranked[0].semantic_score == 1


@pytest.mark.asyncio
async def test_default_bm25_does_not_call_embeddings_even_when_configured(
    database, settings, item_factory
):
    setting = configured(settings, retrieval_mode="bm25")
    with database.begin() as session:
        import_items(session, [item_factory()])
    provider = FixedProvider()
    result = await recommend(database, setting, "帝国火炮", 5, embedding_client=provider)
    assert provider.calls == []
    assert result["retrieval"]["method"] == "bm25"
    assert result["items"][0]["retrieval"]["channels"] == ["lexical"]


@pytest.mark.asyncio
async def test_hybrid_never_weakens_hard_constraints(database, settings, item_factory):
    setting = configured(settings)
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory("10001"),
                item_factory("10002", title="Fixture Empire Overhaul", tags=["Overhaul", "Empire"]),
                item_factory("10003", title="Fixture Cathay Artillery", tags=["Cathay", "Units"]),
                item_factory("10004"),
            ],
        )
        for item_id in ["10001", "10002", "10003", "10004"]:
            record = session.get(Mod, item_id)
            record.chinese_support = "author_claimed" if item_id != "10004" else "unknown"
            index(session, setting, record)
    provider = FixedProvider()
    result = await recommend(
        database, setting, "帝国，增加火炮，必须有汉化，不要大修", 5, embedding_client=provider
    )
    assert [item["mod"]["id"] for item in result["items"]] == ["10001"]
    assert result["retrieval"] == {
        "method": "hybrid_rrf",
        "requested_method": "hybrid_rrf",
        "candidate_count": 1,
        "eligible_count": 1,
        "indexed_count": 1,
        "stale_count": 0,
    }
    assert result["items"][0]["retrieval"]["channels"] == ["lexical", "semantic"]
    with database() as session:
        assert session.get(RecommendationRun, result["run_id"]).result == result


@pytest.mark.asyncio
async def test_semantic_only_candidate_keeps_honest_provenance(database, settings, item_factory):
    setting = configured(settings)
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory(
                    title="Fixture Panorama Controller",
                    tags=["UI"],
                    description="Camera position controls.",
                )
            ],
        )
        index(session, setting, session.get(Mod, "10001"))
    result = await recommend(
        database, setting, "希望观战更有临场感", 5, embedding_client=FixedProvider()
    )
    assert result["engine"] == "rules"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["retrieval"]["channels"] == ["semantic"]
    assert item["retrieval"]["matched_terms"] == []
    assert not any("前段匹配" in reason for reason in item["reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing", "stale", "model", "dimension", "provider", "timeout"]
)
async def test_hybrid_fallback_is_explicit_and_never_uses_bad_index(
    database, settings, item_factory, failure
):
    setting = configured(settings, embedding_timeout_seconds=0.01)
    with database.begin() as session:
        import_items(session, [item_factory()])
        record = session.get(Mod, "10001")
        if failure != "missing":
            index(session, setting, record, vector=[1, 0, 0] if failure == "dimension" else [1, 0])
            session.flush()
            row = session.get(ModEmbedding, (record.id, embedding_namespace(setting)))
            if failure == "stale":
                row.document_hash = "obsolete"
            if failure == "model":
                row.model = "wrong-model"

    class Provider(FixedProvider):
        async def embed(self, texts):
            if failure == "provider":
                raise EmbeddingUnavailable("secret credential and upstream payload")
            if failure == "timeout":
                await asyncio.sleep(0.1)
            return await super().embed(texts)

    provider = Provider()
    result = await recommend(database, setting, "帝国火炮", 5, embedding_client=provider)
    assert result["status"] == "completed"
    assert result["retrieval"]["method"] == "bm25"
    assert result["retrieval"]["requested_method"] == "hybrid_rrf"
    assert result["items"][0]["retrieval"]["channels"] == ["lexical"]
    assert any("退回 BM25" in warning for warning in result["warnings"])
    assert "secret" not in str(result)
    if failure in {"missing", "stale", "model"}:
        assert provider.calls == []


@pytest.mark.asyncio
async def test_source_change_during_query_cannot_publish_stale_candidate(
    database, settings, item_factory
):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory()])
        index(session, setting, session.get(Mod, "10001"))

    class Provider(FixedProvider):
        async def embed(self, texts):
            with database.begin() as session:
                session.get(Mod, "10001").title = "Fixture changed source text"
            return await super().embed(texts)

    result = await recommend(database, setting, "帝国火炮", 5, embedding_client=Provider())
    assert result["items"] == []
    assert any("过期候选" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_empty_eligible_set_never_calls_provider(database, settings, item_factory):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory()])
    provider = FixedProvider()
    result = await recommend(
        database, setting, "必须有汉化的帝国火炮", 5, embedding_client=provider
    )
    assert result["items"] == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_backfill_skips_current_vectors_and_refreshes_source_changes(
    database, settings, item_factory
):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory("10001"), item_factory("10002")])
    provider = FixedProvider()
    first = await backfill_embeddings(database, setting, limit=1, client=provider)
    second = await backfill_embeddings(database, setting, limit=1, client=provider)
    third = await backfill_embeddings(database, setting, client=provider)
    assert (first["generated"], first["remaining"]) == (1, 1)
    assert (second["generated"], second["skipped"]) == (1, 1)
    assert (third["generated"], third["skipped"]) == (0, 2)
    assert len(provider.calls) == 2
    with database.begin() as session:
        session.get(Mod, "10001").description = "Updated source description"
    fourth = await backfill_embeddings(database, setting, client=provider)
    assert fourth["generated"] == 1
    assert fourth["skipped"] == 1


@pytest.mark.asyncio
async def test_backfill_checks_source_again_after_network(database, settings, item_factory):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Provider(FixedProvider):
        async def embed(self, texts):
            with database.begin() as session:
                session.get(Mod, "10001").title = "Changed while request was in flight"
            return await super().embed(texts)

    result = await backfill_embeddings(database, setting, client=Provider())
    assert result["status"] == "partial"
    assert (result["generated"], result["source_changed"], result["remaining"]) == (0, 1, 1)
    with database() as session:
        assert session.get(ModEmbedding, ("10001", embedding_namespace(setting))) is None


@pytest.mark.asyncio
async def test_backfill_keeps_committed_batch_when_next_provider_call_fails(
    database, settings, item_factory
):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory("10001"), item_factory("10002")])

    class Provider(FixedProvider):
        async def embed(self, texts):
            if self.calls:
                raise EmbeddingUnavailable("source payload must not leak")
            return await super().embed(texts)

    result = await backfill_embeddings(database, setting, batch_size=1, client=Provider())
    assert result["status"] == "partial"
    assert (result["generated"], result["remaining"]) == (1, 1)
    assert "source payload" not in str(result)


@pytest.mark.asyncio
async def test_catalog_scan_cap_is_explicit_and_does_not_claim_unscanned_results(
    database, settings, item_factory
):
    with database.begin() as session:
        import_items(
            session,
            [item_factory(str(10001 + i)) for i in range(100)]
            + [
                item_factory(
                    "99999", title="Fixture raretarget", description="raretarget", tags=[]
                ),
            ],
        )
    setting = settings.model_copy(update={"retrieval_catalog_limit": 100})
    result = await recommend(database, setting, "raretarget", 5)
    assert result["items"] == []
    assert result["retrieval"]["eligible_count"] == 100
    assert any("未扫描" in warning for warning in result["warnings"])
    complete = await recommend(database, settings, "raretarget", 5)
    assert [item["mod"]["id"] for item in complete["items"]] == ["99999"]


@pytest.mark.asyncio
async def test_oversized_model_keywords_fall_back_to_bounded_rule_query(
    database, settings, item_factory
):
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Model:
        async def json_response(self, system, data):
            return {"keywords": ["x" * 81]}

    setting = settings.model_copy(update={"llm_api_key": "test-only", "llm_model": "fixture"})
    result = await recommend(database, setting, "帝国火炮", 5, model=Model())
    assert result["engine"] == "rules"
    assert result["items"]
    assert result["constraints"]["keywords"] == []


@pytest.mark.asyncio
async def test_partial_semantic_coverage_retains_lexical_candidates(
    database, settings, item_factory
):
    setting = configured(settings)
    with database.begin() as session:
        import_items(session, [item_factory("10001"), item_factory("10002")])
        index(session, setting, session.get(Mod, "10001"))
    result = await recommend(database, setting, "帝国火炮", 5, embedding_client=FixedProvider())
    assert result["retrieval"]["method"] == "hybrid_rrf"
    assert result["retrieval"]["indexed_count"] == 1
    assert {item["mod"]["id"] for item in result["items"]} == {"10001", "10002"}
    assert any("未索引或过期" in warning for warning in result["warnings"])
