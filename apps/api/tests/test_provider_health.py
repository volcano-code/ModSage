from fastapi.testclient import TestClient

from modsage.main import create_app
from modsage.provider_usage import ProviderUsageStore


def test_health_reports_configuration_separately_from_actual_provider_observation(settings):
    configured = settings.model_copy(
        update={
            "llm_api_key": "private-test-key",
            "llm_base_url": "https://provider.invalid/v1",
            "llm_model": "private-test-model",
        }
    )
    with TestClient(create_app(configured)) as client:
        store = ProviderUsageStore(client.app.state.session_factory, configured)
        initial = client.get("/api/v1/health")
        assert initial.headers["Cache-Control"] == "no-store"
        assert initial.json()["llm_configured"] is True
        assert initial.json()["providers"]["llm"] == {
            "configuration": "configured",
            "calls_enabled": True,
            "last_call": None,
        }
        call = store.reserve(provider="llm", operation="understand")
        pending = client.get("/api/v1/health").json()["providers"]["llm"]["last_call"]
        assert pending["status"] == "started"
        assert pending["completed_at"] is None
        store.finish(call, status="succeeded", latency_ms=50, returned_model="private-test-model")
        response = client.get("/api/v1/health")
        assert response.json()["providers"]["llm"]["last_call"]["status"] == "succeeded"
        for private in [
            "private-test-key",
            "private-test-model",
            "provider.invalid",
            call,
            "daily_call_limit",
            "configuration_id",
            "request_id",
        ]:
            assert private not in response.text

    rotated = configured.model_copy(update={"llm_api_key": "rotated-test-key"})
    with TestClient(create_app(rotated)) as client:
        assert client.get("/api/v1/health").json()["providers"]["llm"]["last_call"] is None


def test_invalid_base_is_not_announced_as_a_configured_model(settings):
    invalid = settings.model_copy(
        update={
            "llm_api_key": "private-key",
            "llm_model": "test",
            "llm_base_url": "https://secret:password@host/v1",
        }
    )
    with TestClient(create_app(invalid)) as client:
        response = client.get("/api/v1/health")
        assert response.json()["llm_configured"] is False
        assert response.json()["providers"]["llm"]["configuration"] == "invalid"
        assert "password" not in response.text
        assert (
            client.post("/api/v1/recommendations", json={"query": "帝国火炮"}).json()["engine"]
            == "rules"
        )
