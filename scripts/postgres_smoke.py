"""Exercise the actual API against the explicit CI PostgreSQL database."""

import os

from fastapi.testclient import TestClient

from modsage.main import app


def main() -> None:
    database_url = os.environ.get("MODSAGE_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        raise SystemExit("Set MODSAGE_DATABASE_URL to the isolated PostgreSQL test database")
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        health.raise_for_status()
        stats = client.get("/api/v1/stats")
        stats.raise_for_status()
        response = client.post(
            "/api/v1/recommendations",
            json={"query": "寻找帝国兵种相关内容，不要大修", "limit": 3},
        )
        response.raise_for_status()
        run = response.json()
        assert run["run_id"], "Recommendation must have a persistent run ID"
        saved = client.get(f"/api/v1/runs/{run['run_id']}")
        saved.raise_for_status()
        assert saved.json()["run_id"] == run["run_id"]
        client.get("/api/v1/digests/latest").raise_for_status()
    print("PostgreSQL API smoke passed: health, catalog stats, persisted run, digest")


if __name__ == "__main__":
    main()
