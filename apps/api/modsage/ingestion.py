import hashlib
import json
import re
import uuid

from sqlalchemy import delete, select

from modsage.catalog import classify, iso
from modsage.models import IngestionRun, MetricSnapshot, Mod, ModRelation, SourceSnapshot, utcnow


def digest_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def import_items(session, items) -> dict:
    """Import an already validated batch atomically; caller owns transaction/commit."""
    created, changed, unchanged = 0, 0, 0
    for item in items:
        from modsage.sources.steam import clean_description

        description = clean_description(item.description)
        payload = {
            "title": item.title,
            "description": description,
            "tags": sorted(item.tags),
            "author": item.author,
            "required_ids": sorted(item.required_ids),
            "updated_at": iso(item.updated_at),
        }
        content_hash = digest_hash(payload)
        summary_hash = digest_hash({k: payload[k] for k in ("title", "description", "tags")})
        mod = session.get(Mod, item.id)
        if mod is None:
            mod = Mod(id=item.id, source_url=item.source_url, content_hash=content_hash)
            session.add(mod)
            created += 1
        elif mod.content_hash != content_hash:
            changed += 1
        else:
            unchanged += 1
        if mod.summary_hash != summary_hash:
            mod.summary_zh = None
            mod.summary_status = "pending"
            mod.summary_hash = summary_hash
            mod.summary_model = None
            # Native Chinese source excerpt is clearly labelled, never called AI translation.
            han = len(re.findall(r"[\u4e00-\u9fff]", description[:1200]))
            if han >= 40 and han / max(len(description[:1200]), 1) > 0.35:
                mod.summary_zh = description[:1000]
                mod.summary_status = "source_chinese"
        for key in (
            "title",
            "author",
            "source_url",
            "preview_url",
            "published_at",
            "updated_at",
            "fetched_at",
            "subscribers",
            "favorites",
        ):
            setattr(mod, key, getattr(item, key))
        mod.description = description
        mod.tags = item.tags
        mod.category = classify(item.title, item.tags)
        mod.content_hash = content_hash
        # A Chinese description does not prove Chinese in-game support or compatibility.
        mod.chinese_support = mod.chinese_support or "unknown"
        mod.compatibility_status = mod.compatibility_status or "unknown"
        preserved_summary_evidence = [
            e
            for e in (mod.evidence or [])
            if e.get("field") == "summary_zh" and mod.summary_status == "generated"
        ]
        mod.evidence = [
            {
                "id": f"{item.id}:{content_hash[:12]}:description",
                "field": "description",
                "value": description[:1800],
                "source_url": item.source_url,
                "excerpt": description[:1800],
                "observed_at": iso(item.fetched_at),
            }
        ] + preserved_summary_evidence
        session.flush()
        raw = json.loads(json.dumps(item.raw, default=str))
        payload_hash = digest_hash(raw)
        exists = session.scalar(
            select(SourceSnapshot.id).where(
                SourceSnapshot.mod_id == item.id, SourceSnapshot.payload_hash == payload_hash
            )
        )
        if exists is None:
            session.add(
                SourceSnapshot(
                    mod_id=item.id, payload_hash=payload_hash, raw=raw, observed_at=item.fetched_at
                )
            )
        mod.dependencies_known = bool(
            getattr(item, "dependencies_known", False)
            or item.raw.get("_modsage_dependencies_available", False)
        )
        if mod.dependencies_known:
            session.execute(
                delete(ModRelation).where(
                    ModRelation.mod_id == item.id, ModRelation.kind == "requires"
                )
            )
            for required_id in dict.fromkeys(item.required_ids):
                session.add(
                    ModRelation(
                        mod_id=item.id,
                        kind="requires",
                        target_id=required_id,
                        source_url=item.source_url,
                        observed_at=item.fetched_at,
                    )
                )
        date = item.fetched_at.date().isoformat()
        metric = session.scalar(
            select(MetricSnapshot).where(
                MetricSnapshot.mod_id == item.id, MetricSnapshot.date == date
            )
        )
        if metric is None:
            metric = MetricSnapshot(mod_id=item.id, date=date)
            session.add(metric)
        metric.subscribers = item.subscribers
        metric.favorites = item.favorites
        metric.observed_at = item.fetched_at
    session.flush()
    return {"created": created, "changed": changed, "unchanged": unchanged, "imported": len(items)}


async def sync_ids(session_factory, steam, ids: list[str], mode="ids") -> dict:
    run_id = str(uuid.uuid4())
    with session_factory.begin() as session:
        session.add(IngestionRun(id=run_id, status="running", mode=mode, requested_ids=ids))
    try:
        items = await steam.get_details(ids)
        warnings = list(getattr(steam, "last_warnings", []))
        with session_factory.begin() as session:
            counts = import_items(session, items)
            run = session.get(IngestionRun, run_id)
            run.status = "completed" if items else "empty"
            run.completed_at = utcnow()
            run.imported_count = len(items)
            run.warnings = warnings
        return {"run_id": run_id, **counts, "warnings": warnings, "mode": mode}
    except Exception as exc:
        with session_factory.begin() as session:
            run = session.get(IngestionRun, run_id)
            run.status = "failed"
            run.completed_at = utcnow()
            # Never persist URLs with keys or arbitrary provider response bodies.
            run.warnings = [f"Source synchronization failed ({type(exc).__name__})"]
        raise
