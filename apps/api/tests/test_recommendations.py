import asyncio

import pytest

from modsage.catalog import FACTIONS
from modsage.ingestion import import_items
from modsage.llm import ModelUnavailable
from modsage.models import RecommendationRun
from modsage.recommendations import parse_rules, recommend


@pytest.mark.parametrize(
    "query", ["帝国火炮，不要大修", "不想装大型重做的帝国模组", "Empire units without overhaul"]
)
def test_rule_parser_preserves_negative_preferences(query):
    assert "overhaul" in parse_rules(query).excluded_categories


def test_preferred_chinese_is_not_required():
    assert parse_rules("帝国模组，最好有中文").chinese_required is False
    assert parse_rules("只要有汉化的帝国模组").chinese_required is True


def test_campaign_name_is_not_a_positive_faction_constraint():
    assert parse_rules("Immortal Empires campaign improvements").factions == []


def test_all_known_factions_can_be_requested_without_schema_failure():
    query = "，".join(variants[0] for variants in FACTIONS.values())
    assert set(parse_rules(query).factions) == set(FACTIONS)


@pytest.mark.parametrize(
    ("query", "required", "excluded"),
    [
        ("我想玩帝国，增加火炮和兵种，不要大修", ["artillery", "units"], ["overhaul"]),
        ("不要火炮", [], ["artillery"]),
        ("不要新增兵种，只改界面", [], ["units"]),
        ("兵种保持原版只改界面", [], ["units"]),
        ("不要增加火炮，只增加兵种", ["units"], ["artillery"]),
        ("Add more Empire units, but no new artillery", ["units"], ["artillery"]),
        ("帝国火炮特效和兵种外观", [], []),
    ],
)
def test_content_requirements_preserve_addition_intent_and_negation(query, required, excluded):
    constraints = parse_rules(query)
    assert constraints.required_topics == required
    assert constraints.excluded_categories == excluded


@pytest.mark.asyncio
async def test_langgraph_filters_exclusions_and_persists_result(database, settings, item_factory):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory("10001"),
                item_factory(
                    "10002",
                    title="Fixture Empire Overhaul",
                    description="Empire artillery overhaul",
                    tags=["mod", "Overhaul"],
                ),
            ],
        )
    result = await recommend(database, settings, "帝国火炮，不要大修", 5)
    assert result["engine"] == "rules"
    assert [r["mod"]["id"] for r in result["items"]] == ["10001"]
    assert result["items"][0]["caveats"]
    with database() as session:
        assert session.get(RecommendationRun, result["run_id"]).result == result


@pytest.mark.asyncio
async def test_unknown_chinese_support_cannot_satisfy_must_have(database, settings, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory()])
    result = await recommend(database, settings, "必须有汉化的帝国火炮模组", 5)
    assert result["items"] == []
    assert result["warnings"]


@pytest.mark.asyncio
async def test_immortal_empires_is_not_empire_faction(database, settings, item_factory):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory(
                    title="Fixture Cathay units",
                    description="Adds Cathay artillery for Immortal Empires.",
                    tags=["mod", "Cathay"],
                )
            ],
        )
    result = await recommend(database, settings, "帝国火炮", 5)
    assert result["items"] == []


@pytest.mark.asyncio
async def test_model_cannot_weaken_rule_exclusions(database, settings, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory(title="Fixture Empire Overhaul", tags=["Overhaul"])])

    class Model:
        async def json_response(self, system, data):
            return {"factions": ["empire"], "excluded_categories": [], "chinese_required": False}

    configured = settings.model_copy(update={"llm_api_key": "test-only", "llm_model": "fixture"})
    result = await recommend(database, configured, "帝国火炮，不要大修", 5, model=Model())
    assert result["engine"] == "llm"
    assert result["items"] == []
    assert "overhaul" in result["constraints"]["excluded_categories"]


@pytest.mark.asyncio
async def test_model_failure_falls_back_honestly(database, settings, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Model:
        async def json_response(self, system, data):
            raise ModelUnavailable("test failure")

    configured = settings.model_copy(update={"llm_api_key": "test-only", "llm_model": "fixture"})
    result = await recommend(database, configured, "帝国火炮", 5, model=Model())
    assert result["engine"] == "rules"
    assert result["items"]
    assert any("降级" in x for x in result["warnings"])


@pytest.mark.asyncio
async def test_task_timeout_is_persisted(database, settings):
    class Model:
        async def json_response(self, system, data):
            await asyncio.sleep(1)

    configured = settings.model_copy(
        update={
            "llm_api_key": "test-only",
            "llm_model": "fixture",
            "recommendation_timeout_seconds": 0.01,
        }
    )
    result = await recommend(database, configured, "帝国火炮", 5, model=Model())
    assert result["status"] == "failed"
    with database() as session:
        assert session.get(RecommendationRun, result["run_id"]).status == "failed"


@pytest.mark.asyncio
async def test_primary_faction_and_content_exclude_promotions_and_visual_mods(
    database, settings, item_factory
):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory("10001"),
                item_factory(
                    "10002",
                    title="Fixture High Elves Heroes Compilation",
                    tags=["mod", "units"],
                    description="High Elf heroes. Can also be recruited by Empire. Other work: artillery units.",
                ),
                item_factory(
                    "10003",
                    title="Fixture Heroes for Vampire Counts",
                    tags=["mod", "units"],
                    description="Adds vampire heroes.\nOther compilations coming soon: Empire artillery units.",
                ),
                item_factory(
                    "10004",
                    title="Fixture Empire Artillery LOD Enhanced",
                    tags=["mod", "graphical"],
                    description="Improves the visuals of Empire artillery units; adds no new content.",
                ),
                item_factory(
                    "10005",
                    title="Fixture Empire Infantry",
                    tags=["mod", "units"],
                    description="Adds Empire infantry units.\nOther mods: Empire artillery and cannons.",
                ),
            ],
        )
    result = await recommend(database, settings, "我想玩帝国，增加火炮和兵种，不要大修", 5)
    assert [item["mod"]["id"] for item in result["items"]] == ["10001"]
    assert result["constraints"]["required_topics"] == ["artillery", "units"]


@pytest.mark.asyncio
async def test_missing_core_evidence_returns_empty_instead_of_padding(
    database, settings, item_factory
):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory(
                    title="Fixture Visual Effects",
                    tags=["mod", "graphical"],
                    description="Visual distance changes. "
                    + "Rendering details. " * 100
                    + "Other mods: Empire artillery units.",
                )
            ],
        )
    result = await recommend(database, settings, "我想玩帝国，增加火炮和兵种，不要大修", 5)
    assert result["items"] == []
    assert any("没有可确认满足条件" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_model_cannot_remove_content_requirements(database, settings, item_factory):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory(
                    title="Fixture Empire Infantry",
                    tags=["units"],
                    description="Adds Empire infantry units.",
                )
            ],
        )

    class Model:
        async def json_response(self, system, data):
            return {"factions": ["empire"], "required_topics": []}

    configured = settings.model_copy(update={"llm_api_key": "test-only", "llm_model": "fixture"})
    result = await recommend(database, configured, "帝国，增加火炮和兵种", 5, model=Model())
    assert result["engine"] == "llm"
    assert result["items"] == []
    assert result["constraints"]["required_topics"] == ["artillery", "units"]


@pytest.mark.asyncio
async def test_excluding_new_units_still_allows_visual_changes(database, settings, item_factory):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory("10001"),
                item_factory(
                    "10002",
                    title="Fixture Empire Unit Reskins",
                    tags=["graphical"],
                    description="Changes Empire unit visuals without adding units.",
                ),
            ],
        )
    result = await recommend(database, settings, "帝国，兵种保持原版，只想改兵种外观", 5)
    assert [item["mod"]["id"] for item in result["items"]] == ["10002"]
