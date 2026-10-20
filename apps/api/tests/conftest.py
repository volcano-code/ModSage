from datetime import datetime, timedelta, timezone

import pytest

from modsage.config import Settings
from modsage.database import init_database, make_database
from modsage.sources.steam import SteamItem


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        steam_api_key="",
        llm_api_key="",
        llm_model="",
        admin_token="",
    )


@pytest.fixture
def database(settings):
    engine, factory = make_database(settings.database_url)
    init_database(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def item_factory():
    def make(item_id="10001", **kwargs):
        now = datetime.now(timezone.utc)
        payload = {
            "id": item_id,
            "title": "Fixture Empire Artillery",
            "description": "TEST FIXTURE: Adds artillery units for the Empire.",
            "author": "fixture-author",
            "source_url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}",
            "preview_url": None,
            "tags": ["mod", "Units", "Empire"],
            "published_at": now - timedelta(days=60),
            "updated_at": now - timedelta(days=2),
            "fetched_at": now,
            "subscribers": 120,
            "favorites": 3,
            "required_ids": [],
            "dependencies_known": True,
        }
        payload.update(kwargs)
        payload["raw"] = kwargs.get(
            "raw",
            {
                "publishedfileid": item_id,
                "title": payload["title"],
                "description": payload["description"],
                "subscriptions": payload["subscribers"],
                "_modsage_dependencies_available": payload["dependencies_known"],
            },
        )
        return SteamItem(**payload)

    return make
