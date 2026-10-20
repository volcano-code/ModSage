from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from modsage.catalog import iso, serialize_mod
from modsage.models import Digest, Mod, utcnow


def build_digest(session, now=None, refresh=False) -> dict:
    now = now or utcnow()
    date = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    existing = session.get(Digest, date)
    if existing and not refresh:
        return existing.result
    mods = session.scalars(select(Mod)).all()
    # Conservative coverage cutoff: refreshing one item must not refresh the whole catalog.
    cutoff = min((iso(mod.fetched_at) for mod in mods), default=None)
    since = iso(now - timedelta(hours=24))
    until = iso(now)
    new = [m for m in mods if m.published_at and since <= iso(m.published_at) <= until]
    new.sort(key=lambda m: iso(m.published_at), reverse=True)
    updated = [m for m in mods if m.updated_at and since <= iso(m.updated_at) <= until]
    updated.sort(key=lambda m: iso(m.updated_at), reverse=True)
    # A one-time current subscription count is not a recent growth/trending signal.
    popular = [m for m in mods if m.subscribers is not None]
    popular.sort(key=lambda m: (m.subscribers, m.id), reverse=True)
    warnings = [
        "内容仅覆盖当前收录目录。排行按可用订阅总量排序，不代表 Steam 实时趋势或近期增量。",
        "最近更新依据来源时间；没有更新说明时不能确认具体游戏内容发生了变化。",
    ]
    if not mods:
        warnings.append("目录尚未同步真实 Mod，本期暂无可展示内容。")
    elif cutoff and cutoff < iso(now - timedelta(hours=24)):
        warnings.append("来源已超过 24 小时未成功同步，请以原始 Workshop 页面为准。")
    result = {
        "date": date,
        "generated_at": iso(now),
        "source_cutoff": cutoff,
        "sections": [
            {
                "key": "new",
                "title": "24 小时内新发布",
                "items": [serialize_mod(session, m) for m in new[:6]],
            },
            {
                "key": "updated",
                "title": "24 小时内更新",
                "items": [serialize_mod(session, m) for m in updated[:6]],
            },
            {
                "key": "popular",
                "title": "目录内订阅量排行",
                "items": [serialize_mod(session, m) for m in popular[:6]],
            },
        ],
        "warnings": warnings,
    }
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(Digest).values(date=date, generated_at=now, result=result)
    if refresh:
        statement = statement.on_conflict_do_update(
            index_elements=[Digest.date],
            set_={"generated_at": now, "result": result},
            where=Digest.generated_at <= now,
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=[Digest.date])
    session.execute(statement)
    session.flush()
    session.expire_all()
    return session.get(Digest, date).result


def validate_modlist(session, ids: list[str]) -> dict:
    from modsage.models import ModRelation

    selected = set(ids)
    visited = set()
    missing = []
    conflicts = []
    warnings = [
        "检查仅使用已收录的关系证据，不能证明实际兼容性或存档稳定性。",
        "前置关系不代表加载顺序；请遵循作者的明确说明。",
    ]
    stack = list(selected)
    while stack:
        mod_id = stack.pop()
        if mod_id in visited:
            continue
        visited.add(mod_id)
        mod = session.get(Mod, mod_id)
        if mod is None:
            warnings.append(f"{mod_id} 未收录，无法检查其前置与内容。")
            continue
        if not mod.dependencies_known:
            warnings.append(f"{mod.title} 最近响应未提供完整前置字段，已有关系仍需核对。")
        relations = session.scalars(select(ModRelation).where(ModRelation.mod_id == mod_id)).all()
        for relation in relations:
            if relation.kind == "requires":
                if relation.target_id not in selected:
                    missing.append({"mod_id": mod_id, "required_id": relation.target_id})
                if relation.target_id not in visited:
                    stack.append(relation.target_id)
            elif relation.kind == "conflicts_with" and relation.target_id in selected:
                pair = sorted([mod_id, relation.target_id])
                if not any(c["mod_ids"] == pair for c in conflicts):
                    conflicts.append({"mod_ids": pair, "source_url": relation.source_url})
    return {
        "mod_ids": list(dict.fromkeys(ids)),
        "missing_dependencies": missing,
        "known_conflicts": conflicts,
        "unknown_compatibility": sorted(selected),
        "warnings": list(dict.fromkeys(warnings)),
    }
