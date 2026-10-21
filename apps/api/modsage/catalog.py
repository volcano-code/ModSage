import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from modsage.models import Mod, ModRelation

FACTIONS = {
    "empire": ["帝国", "empire", "imperial"],
    "cathay": ["震旦", "cathay", "cathayan"],
    "dwarfs": ["矮人", "dwarf", "dwarfs", "dwarven"],
    "kislev": ["基斯里夫", "基斯列夫", "kislev"],
    "skaven": ["鼠人", "斯卡文", "skaven"],
    "high_elves": ["高等精灵", "高精", "high elves", "high elf"],
    "dark_elves": ["黑暗精灵", "暗精", "dark elves", "dark elf"],
    "lizardmen": ["蜥蜴人", "蜥蜴", "lizardmen"],
    "greenskins": ["绿皮", "兽人", "greenskins", "orcs"],
    "chaos": ["混沌", "chaos"],
    "bretonnia": ["巴托", "bretonnia", "bretonnian"],
    "vampires": ["吸血鬼", "vampire"],
}
TOPICS = {
    "炮": ["artillery", "cannon", "mortar", "炮"],
    "兵种": ["unit", "units", "兵种"],
    "界面": ["interface", "ui", "界面"],
    "地图": ["map", "maps", "地图"],
    "镜头": ["camera", "镜头"],
    "画面": ["visual", "texture", "reskin", "画面"],
    "战役": ["campaign", "战役"],
    "战斗": ["battle", "combat", "战斗"],
    "汉化": ["translation", "chinese", "汉化", "中文"],
    "大修": ["overhaul", "大修", "重做"],
}


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def classify(title: str, tags: list[str]) -> str:
    text = (title + " " + " ".join(tags)).lower()
    for category, markers in [
        ("translation", ["translation", "localization", "汉化", "中文翻译"]),
        ("overhaul", ["overhaul", "grimhammer", "radious", "大修"]),
        ("ui", ["interface", "ui", "camera", "界面", "镜头"]),
        ("visual", ["visual", "reskin", "texture", "graphical", "美化"]),
        ("units", ["unit", "units", "兵种", "artillery", "cannon", "cannons"]),
        ("campaign", ["campaign", "faction", "战役"]),
        ("battle", ["battle", "combat", "战斗"]),
    ]:
        if any(contains_term(marker, text) for marker in markers):
            return category
    return "other"


def search_terms(query: str) -> list[str]:
    text = query.lower().strip()
    stop = {
        "the",
        "and",
        "for",
        "with",
        "without",
        "want",
        "recommend",
        "mods",
        "mod",
        "please",
        "some",
        "not",
        "no",
        "any",
        "need",
    }
    result = [t for t in re.findall(r"[a-z0-9_]{2,}", text) if t not in stop]
    for variants in FACTIONS.values():
        if any(contains_term(v, text) for v in variants):
            result.extend(variants)
    for key, variants in TOPICS.items():
        if key in text or any(contains_term(v, text) for v in variants):
            result.extend(variants)
    # Preserve Chinese product names; drop conversational filler for smaller tokens.
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(chunk) <= 8:
            result.append(chunk)
    return list(dict.fromkeys(result))[:80]


def mod_text(mod: Mod) -> str:
    return (" ".join([mod.title, mod.description, mod.summary_zh or "", *mod.tags])).lower()


def contains_term(term: str, text: str) -> bool:
    if re.fullmatch(r"[a-z0-9_ ]+", term):
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))
    return term in text


def lexical_score(mod: Mod, terms: list[str]) -> float:
    title = mod.title.lower()
    tags = " ".join(mod.tags).lower()
    text = mod_text(mod)
    return sum(
        3
        if contains_term(term, title)
        else 2
        if contains_term(term, tags)
        else 1
        if contains_term(term, text)
        else 0
        for term in terms
    )


def serialize_mod(session, mod: Mod) -> dict:
    relations = session.scalars(select(ModRelation).where(ModRelation.mod_id == mod.id)).all()
    return {
        "id": mod.id,
        "title": mod.title,
        "summary_zh": mod.summary_zh,
        "description_excerpt": mod.description[:1800],
        "author": mod.author,
        "source_url": mod.source_url,
        "preview_url": mod.preview_url,
        "tags": mod.tags,
        "category": mod.category,
        "published_at": iso(mod.published_at),
        "updated_at": iso(mod.updated_at),
        "fetched_at": iso(mod.fetched_at),
        "subscribers": mod.subscribers,
        "favorites": mod.favorites,
        "chinese_support": mod.chinese_support,
        "summary_status": mod.summary_status,
        "compatibility_status": mod.compatibility_status,
        "dependencies_known": mod.dependencies_known,
        "source_kind": "steam",
        "evidence": mod.evidence,
        "relations": [
            {"kind": rel.kind, "target_id": rel.target_id, "source_url": rel.source_url}
            for rel in relations
        ],
    }


def list_mods(session, *, q="", category=None, sort="updated", page=1, page_size=12):
    statement = select(Mod)
    if category:
        statement = statement.where(Mod.category == category)
    if sort == "popular":
        statement = statement.where(Mod.subscribers.is_not(None))
    if q.strip():
        from modsage.retrieval import rank_bm25

        rows = session.scalars(statement).all()
        scored = [(item.mod, item.score) for item in rank_bm25(rows, q, include_summary=True)]
        if sort == "popular":
            scored.sort(key=lambda x: (x[0].subscribers or 0, x[1]), reverse=True)
        elif sort == "newest":
            scored.sort(key=lambda x: (iso(x[0].published_at) or "", x[1]), reverse=True)
        elif sort == "updated":
            scored.sort(key=lambda x: (iso(x[0].updated_at) or "", x[1]), reverse=True)
        else:
            scored.sort(key=lambda x: (x[1], iso(x[0].updated_at) or "", x[0].id), reverse=True)
        total = len(scored)
        rows = [m for m, _ in scored[(page - 1) * page_size : page * page_size]]
    else:
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        order = {
            "updated": Mod.updated_at.desc().nullslast(),
            "newest": Mod.published_at.desc().nullslast(),
            "popular": Mod.subscribers.desc().nullslast(),
            "relevant": Mod.updated_at.desc().nullslast(),
        }[sort]
        rows = session.scalars(
            statement.order_by(order, Mod.id).offset((page - 1) * page_size).limit(page_size)
        ).all()
    return {
        "items": [serialize_mod(session, mod) for mod in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
