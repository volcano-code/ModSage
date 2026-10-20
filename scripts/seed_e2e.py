"""Create explicitly synthetic browser fixtures in an isolated E2E database only."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from modsage.database import init_database, make_database
from modsage.ingestion import import_items
from modsage.sources.steam import SteamItem


def main() -> None:
    url = os.environ.get("MODSAGE_DATABASE_URL", "")
    parsed = make_url(url)
    if (
        os.environ.get("MODSAGE_E2E") != "1"
        or not parsed.drivername.startswith("sqlite")
        or Path(parsed.database or "").name != "modsage-e2e.db"
    ):
        raise SystemExit("Refusing to seed: use MODSAGE_E2E=1 and modsage-e2e.db")
    now = datetime.now(UTC)
    fixtures = [
        (
            "900000000001",
            "E2E Empire Artillery｜帝国火炮扩展",
            (
                "仅供自动化测试，非真实 Workshop 模组。这个测试条目提供帝国火炮兵种扩展，"
                "用于检查目录搜索、中文详情、清单检查和推荐结果。游戏内汉化和兼容性均未核实。"
            ),
            ["Units", "Empire"],
            ["900000000099"],
        ),
        (
            "900000000002",
            "E2E Campaign Overhaul｜战役大修",
            (
                "仅供自动化测试，非真实 Workshop 模组。这个测试条目属于大型战役重做，"
                "会改变多个系统，用于验证用户提出不要大修时排除相关内容。任何兼容信息均未确认。"
            ),
            ["Overhaul", "Campaign"],
            [],
        ),
    ]
    items = []
    for mod_id, title, description, tags, required_ids in fixtures:
        item = SteamItem(
            id=mod_id,
            title=title,
            description=description,
            author="ModSage automated test fixture",
            source_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}",
            preview_url=None,
            tags=tags,
            published_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
            fetched_at=now,
            subscribers=None,
            favorites=None,
            required_ids=required_ids,
            raw={
                "synthetic_test_fixture": True,
                "children": required_ids,
                "_modsage_dependencies_available": True,
            },
        )
        if hasattr(item, "dependencies_known"):
            from dataclasses import replace

            item = replace(item, dependencies_known=True)
        items.append(item)
    engine, factory = make_database(url)
    init_database(engine)
    with factory.begin() as session:
        result = import_items(session, items)
    engine.dispose()
    print(f"Isolated E2E fixtures ready: {result['imported']} explicitly synthetic records")


if __name__ == "__main__":
    main()
