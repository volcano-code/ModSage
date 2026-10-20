from dataclasses import replace

import pytest
from sqlalchemy import func, select

from modsage.ingestion import import_items, sync_ids
from modsage.models import IngestionRun, MetricSnapshot, Mod, ModRelation, SourceSnapshot
from modsage.sources.steam import SteamSourceError


def test_import_is_idempotent_with_snapshots_and_metrics(database, item_factory):
    item = item_factory(required_ids=["10002"])
    with database.begin() as session:
        assert import_items(session, [item])["created"] == 1
    with database.begin() as session:
        assert import_items(session, [item])["unchanged"] == 1
    with database() as session:
        for model in [Mod, SourceSnapshot, MetricSnapshot, ModRelation]:
            assert session.scalar(select(func.count()).select_from(model)) == 1


def test_content_change_invalidates_summary_but_metrics_do_not(database, item_factory):
    item = item_factory()
    with database.begin() as session:
        import_items(session, [item])
        mod = session.get(Mod, item.id)
        mod.summary_zh = "测试：来自旧内容的中文介绍。"
        mod.summary_status = "generated"
    with database.begin() as session:
        import_items(session, [replace(item, subscribers=999)])
        assert session.get(Mod, item.id).summary_status == "generated"
    with database.begin() as session:
        import_items(session, [replace(item, description="Completely different units for Cathay.")])
        assert session.get(Mod, item.id).summary_status == "pending"
        assert session.get(Mod, item.id).summary_zh is None


def test_unknown_dependencies_do_not_erase_previous_evidence(database, item_factory):
    item = item_factory(required_ids=["10002"], dependencies_known=True)
    with database.begin() as session:
        import_items(session, [item])
    unknown = replace(
        item,
        required_ids=[],
        dependencies_known=False,
        raw={"publishedfileid": item.id, "_modsage_dependencies_available": False},
    )
    with database.begin() as session:
        import_items(session, [unknown])
    with database() as session:
        assert session.get(Mod, item.id).dependencies_known is False
        assert session.scalar(select(ModRelation.target_id)) == "10002"
    # Only a fresh, explicitly complete empty dependency list may clear previous edges.
    known_empty = replace(unknown, dependencies_known=True)
    with database.begin() as session:
        import_items(session, [known_empty])
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ModRelation)) == 0


def test_chinese_source_does_not_prove_in_game_language(database, item_factory):
    item = item_factory(
        description="这是测试用的作者中文说明，介绍新增兵种和火炮的功能。这段文字只能证明页面本身包含中文，不能证明游戏内存在汉化，也不能证明该模组与其他模组兼容。"
    )
    with database.begin() as session:
        import_items(session, [item])
        mod = session.get(Mod, item.id)
        assert mod.summary_status == "source_chinese"
        assert mod.chinese_support == "unknown"
        assert mod.compatibility_status == "unknown"


@pytest.mark.asyncio
async def test_failed_sync_keeps_catalog_and_records_failure(database, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory()])

    class Source:
        async def get_details(self, ids):
            raise SteamSourceError("Upstream unavailable")

    with pytest.raises(SteamSourceError):
        await sync_ids(database, Source(), ["10001"])
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Mod)) == 1
        assert session.scalar(select(IngestionRun.status)) == "failed"


@pytest.mark.asyncio
async def test_successful_sync_commits_audited_result(database, item_factory):
    class Source:
        last_warnings = ["One optional source field is missing"]

        async def get_details(self, ids):
            return [item_factory()]

    result = await sync_ids(database, Source(), ["10001"])
    assert result["imported"] == 1
    assert result["warnings"]
    with database() as session:
        assert session.get(IngestionRun, result["run_id"]).status == "completed"
