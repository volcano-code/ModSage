from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modsage.catalog import iso
from modsage.digests import build_digest, validate_modlist
from modsage.ingestion import import_items
from modsage.models import Digest, ModRelation, utcnow


def test_first_seen_is_not_new_publication(database, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory()])
        result = build_digest(session)
        new = next(s for s in result["sections"] if s["key"] == "new")
        assert new["items"] == []
        assert result["source_cutoff"] is not None


def test_daily_issue_is_idempotent_and_refreshable(database, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory(updated_at=utcnow() - timedelta(hours=1))])
        first = build_digest(session)
        second = build_digest(session)
        assert first == second
        assert session.scalar(select(func.count()).select_from(Digest)) == 1
        assert any(s["items"] for s in first["sections"] if s["key"] == "updated")
        build_digest(session, refresh=True)
        assert session.scalar(select(func.count()).select_from(Digest)) == 1


def test_dependency_closure_preserves_unknowns(database, item_factory):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory("10001", required_ids=["10002"]),
                item_factory("10002", required_ids=["10003"]),
            ],
        )
        result = validate_modlist(session, ["10001"])
        assert {m["required_id"] for m in result["missing_dependencies"]} == {"10002", "10003"}
        assert result["unknown_compatibility"] == ["10001"]
        assert any("10003" in warning for warning in result["warnings"])


def test_dependency_cycle_terminates_and_does_not_claim_compatibility(database, item_factory):
    with database.begin() as session:
        import_items(
            session,
            [
                item_factory("10001", required_ids=["10002"]),
                item_factory("10002", required_ids=["10001"]),
            ],
        )
        result = validate_modlist(session, ["10001", "10002"])
        assert result["missing_dependencies"] == []
        assert len(result["unknown_compatibility"]) == 2


def test_known_conflict_is_evidence_backed_and_deduplicated(database, item_factory):
    with database.begin() as session:
        import_items(session, [item_factory("10001"), item_factory("10002")])
        session.add_all(
            [
                ModRelation(
                    mod_id="10001",
                    target_id="10002",
                    kind="conflicts_with",
                    source_url="https://steamcommunity.com/sharedfiles/filedetails/?id=10001",
                ),
                ModRelation(
                    mod_id="10002",
                    target_id="10001",
                    kind="conflicts_with",
                    source_url="https://steamcommunity.com/sharedfiles/filedetails/?id=10002",
                ),
            ]
        )
        session.flush()
        result = validate_modlist(session, ["10001", "10002"])
        assert len(result["known_conflicts"]) == 1
        assert result["known_conflicts"][0]["source_url"].startswith("https://steamcommunity.com/")


def test_partial_sync_keeps_stale_coverage_warning(database, item_factory):
    now = utcnow()
    oldest = now - timedelta(days=3)
    with database.begin() as session:
        import_items(
            session,
            [item_factory("10001", fetched_at=oldest), item_factory("10002", fetched_at=now)],
        )
        result = build_digest(session, now=now)
        assert result["source_cutoff"] == iso(oldest)
        assert any("24 小时未成功同步" in warning for warning in result["warnings"])


def test_concurrent_first_issue_creates_one_persistent_row(database, monkeypatch):
    first_reads = Barrier(2)
    original_get = Session.get

    def synchronized_get(session, entity, ident, *args, **kwargs):
        result = original_get(session, entity, ident, *args, **kwargs)
        if entity is Digest and result is None:
            first_reads.wait(timeout=5)
        return result

    monkeypatch.setattr(Session, "get", synchronized_get)
    now = utcnow()

    def build():
        with database.begin() as session:
            return build_digest(session, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: build(), range(2)))
    assert results[0] == results[1]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Digest)) == 1
