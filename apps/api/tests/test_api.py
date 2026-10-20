from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from modsage.catalog import iso
from modsage.ingestion import import_items
from modsage.main import create_app
from modsage.models import utcnow


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_empty_catalog_does_not_invent_data(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    stats = client.get("/api/v1/stats").json()
    assert stats["total_mods"] == 0
    assert stats["last_synced_at"] is None
    assert stats["discovery_available"] is False
    assert client.get("/api/v1/mods").json()["items"] == []
    assert client.get("/api/v1/mods/10001").status_code == 404
    digest = client.get("/api/v1/digests/latest").json()
    assert all(not section["items"] for section in digest["sections"])


def test_catalog_pagination_and_search_return_source_fields(client, item_factory):
    with client.app.state.session_factory.begin() as session:
        import_items(
            session,
            [
                item_factory("10001"),
                item_factory(
                    "10002",
                    title="Fixture Dwarf units",
                    tags=["mod", "Dwarfs"],
                    description="Dwarf warriors",
                ),
            ],
        )
    result = client.get("/api/v1/mods", params={"q": "帝国", "sort": "relevant"}).json()
    assert result["total"] == 1
    assert result["items"][0]["id"] == "10001"
    assert result["items"][0]["compatibility_status"] == "unknown"
    assert result["items"][0]["source_kind"] == "steam"
    assert result["items"][0]["evidence"]
    assert client.get("/api/v1/mods?page=0").status_code == 422
    assert client.get("/api/v1/mods?page_size=10000").status_code == 422


def test_popular_does_not_treat_missing_counts_as_zero(client, item_factory):
    with client.app.state.session_factory.begin() as session:
        import_items(
            session, [item_factory("10001", subscribers=None), item_factory("10002", subscribers=0)]
        )
    result = client.get("/api/v1/mods?sort=popular").json()
    assert [m["id"] for m in result["items"]] == ["10002"]


def test_recommendations_and_runs_are_persisted(client, item_factory):
    with client.app.state.session_factory.begin() as session:
        import_items(session, [item_factory()])
    response = client.post("/api/v1/recommendations", json={"query": "帝国火炮"})
    assert response.status_code == 200
    result = response.json()
    assert result["engine"] == "rules"
    saved = client.get("/api/v1/runs/" + result["run_id"])
    assert saved.json() == result
    assert saved.headers["Cache-Control"] == "no-store"
    assert client.post("/api/v1/recommendations", json={"query": "   "}).status_code == 422


def test_management_endpoint_disabled_without_nonempty_token(client):
    response = client.post("/api/v1/admin/sync", json={"mod_ids": ["10001"]})
    assert response.status_code == 503


def test_management_endpoint_rejects_wrong_token(settings):
    configured = settings.model_copy(update={"admin_token": "test-secret"})
    with TestClient(create_app(configured)) as client:
        response = client.post(
            "/api/v1/admin/sync",
            json={"mod_ids": ["10001"]},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401


def test_unsafe_id_and_empty_list_are_rejected(client):
    assert (
        client.post(
            "/api/v1/modlists/validate", json={"mod_ids": ["http://localhost/admin"]}
        ).status_code
        == 422
    )
    assert client.post("/api/v1/modlists/validate", json={"mod_ids": []}).status_code == 422


def test_recommendation_rate_limit_is_enforced(settings):
    configured = settings.model_copy(update={"request_limit_per_minute": 1})
    with TestClient(create_app(configured)) as client:
        assert client.post("/api/v1/recommendations", json={"query": "帝国火炮"}).status_code == 200
        response = client.post("/api/v1/recommendations", json={"query": "帝国火炮"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"


def test_stats_do_not_present_a_partial_sync_as_complete_freshness(client, item_factory):
    oldest = utcnow() - timedelta(days=3)
    with client.app.state.session_factory.begin() as session:
        import_items(session, [item_factory("10001", fetched_at=oldest), item_factory("10002")])
    assert client.get("/api/v1/stats").json()["last_synced_at"] == iso(oldest)


@pytest.mark.parametrize("endpoint", ["/api/v1/modlists/validate", "/api/v1/admin/sync"])
def test_uint64_overflow_is_rejected_before_source_access(client, endpoint):
    response = client.post(endpoint, json={"mod_ids": [str(2**64)]})
    assert response.status_code == 422


def test_authorized_import_refreshes_an_empty_cached_daily_issue(
    settings, item_factory, monkeypatch
):
    item = item_factory()

    class Source:
        last_warnings = []

        def __init__(self, **kwargs):
            pass

        async def get_details(self, ids):
            assert ids == [item.id]
            return [item]

        async def aclose(self):
            pass

    monkeypatch.setattr("modsage.sources.steam.SteamClient", Source)
    configured = settings.model_copy(update={"admin_token": "test-secret"})
    with TestClient(create_app(configured)) as client:
        before = client.get("/api/v1/digests/latest").json()
        assert all(not section["items"] for section in before["sections"])
        response = client.post(
            "/api/v1/admin/sync",
            json={"mod_ids": [item.id]},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert response.status_code == 200
        assert response.json()["imported"] == 1
        after = client.get("/api/v1/digests/latest").json()
        popular = next(section for section in after["sections"] if section["key"] == "popular")
        assert [mod["id"] for mod in popular["items"]] == [item.id]
