"""Deterministic bilingual BM25 and reciprocal-rank fusion for bounded catalogs.

These pure functions do not call providers or relax recommendation constraints.
The caller must select eligible records before either ranking channel is used.
"""

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from modsage.catalog import FACTIONS, TOPICS, contains_term, search_terms

TOKENIZER_VERSION = "bilingual-bm25-v1"
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "i",
    "in",
    "is",
    "it",
    "mod",
    "mods",
    "of",
    "on",
    "or",
    "please",
    "some",
    "that",
    "the",
    "this",
    "to",
    "want",
    "with",
    "without",
    "would",
    "you",
    "your",
    "no",
    "not",
    "any",
    "need",
    "recommend",
    "想玩",
    "想要",
    "希望",
    "推荐",
    "模组",
    "可以",
    "不要",
    "一些",
    "最好",
    "需要",
    "能够",
    "以及",
    "相关",
    "一个",
    "一下",
    "我想",
    "给我",
}
PLURALS = {
    "units": "unit",
    "troops": "troop",
    "cannons": "cannon",
    "mortars": "mortar",
    "dwarfs": "dwarf",
    "dwarves": "dwarf",
    "maps": "map",
    "textures": "texture",
    "reskins": "reskin",
    "battles": "battle",
    "campaigns": "campaign",
}


def core_description(mod) -> str:
    """Keep a bounded author introduction, excluding obvious cross-promotion."""
    lead = mod.description[:1200].lower()
    return re.split(
        r"(?:^|\n)\s*(?:other (?:mods|compilations)|my other mods|check out my|其他模组|我的其他|更多作品)",
        lead,
        maxsplit=1,
    )[0]


def tokenize(text: str) -> list[str]:
    """English words, Chinese bigrams, and explicit bilingual domain aliases.

    No learned tokenizer/model is implied. Canonical domain tokens let a Chinese
    query retrieve English source text without duplicating every synonym.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    result = []
    for word in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]+", text):
        if word.isascii():
            result.append(PLURALS.get(word, word))
        elif len(word) == 1:
            result.append(word)
        else:
            result.extend(word[i : i + 2] for i in range(len(word) - 1))
    result = [word for word in result if word not in STOP_WORDS]
    for kind, groups in (("faction", FACTIONS), ("topic", TOPICS)):
        for key, aliases in groups.items():
            if any(contains_term(alias, text) for alias in aliases):
                result.append(f"{kind}:{key}")
    return result


def document_fields(mod, *, include_summary=False) -> tuple[str, str, str]:
    intro = core_description(mod)
    if include_summary:
        intro += "\n" + (getattr(mod, "summary_zh", None) or "")[:1600]
    return mod.title[:400], " ".join(tag[:128] for tag in mod.tags[:64])[:2048], intro


@dataclass
class RankedCandidate:
    mod: object
    score: float
    lexical_score: float = 0.0
    semantic_score: float | None = None
    matched_terms: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.mod.id

    def evidence(self) -> dict:
        return {
            "lexical_score": round(self.lexical_score, 6),
            "semantic_score": None
            if self.semantic_score is None
            else round(self.semantic_score, 6),
            "matched_terms": self.matched_terms,
            "channels": self.channels,
        }


def rank_bm25(mods, query: str, *, include_summary=False) -> list[RankedCandidate]:
    """Field-weighted BM25: title 3, tags 2, bounded author introduction 1.

    Catalog search may opt into the displayed Chinese summary; recommendation
    ranking and embedding input keep source-only fields by default.
    Document frequency spans all three fields. Length normalization is per field,
    with k1=1.2 and b=0.75. Results and tie breaks are deterministic.
    """
    mods = list(mods)
    # The API accepts 1000 query characters plus at most 12 short model keywords.
    # Cap the pure helper too, so callers cannot create an unbounded token product.
    query = query[:2000]
    query_tokens = list(dict.fromkeys(tokenize(query)))[:256]
    if not mods or not query_tokens:
        return []
    fields = [
        tuple(
            Counter(tokenize(text))
            for text in document_fields(mod, include_summary=include_summary)
        )
        for mod in mods
    ]
    lengths = [tuple(sum(counts.values()) for counts in row) for row in fields]
    averages = [sum(row[i] for row in lengths) / len(mods) or 1.0 for i in range(3)]
    frequencies = {
        token: sum(any(token in field for field in row) for row in fields) for token in query_tokens
    }
    terms = search_terms(query)
    result = []
    for mod, row, sizes in zip(mods, fields, lengths, strict=True):
        score = 0.0
        matched_tokens = []
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            idf = math.log1p((len(mods) - frequency + 0.5) / (frequency + 0.5))
            for index, weight in enumerate((3.0, 2.0, 1.0)):
                tf = row[index][token]
                if tf:
                    denominator = tf + 1.2 * (0.25 + 0.75 * sizes[index] / averages[index])
                    score += weight * idf * tf * 2.2 / denominator
                    if ":" not in token:
                        matched_tokens.append(token)
        if score <= 0:
            continue
        text = " ".join(document_fields(mod, include_summary=include_summary)).lower()
        matched = [term for term in terms if contains_term(term, text)]
        matched.extend(token for token in matched_tokens if contains_term(token, text))
        result.append(
            RankedCandidate(
                mod=mod,
                score=score,
                lexical_score=score,
                matched_terms=list(dict.fromkeys(matched))[:8],
                channels=["lexical"],
            )
        )
    return sorted(result, key=lambda item: (-item.score, item.id))


def fuse_rankings(lexical, semantic, limit: int | None = None) -> list[RankedCandidate]:
    """Equal-weight reciprocal-rank fusion (k=60), independent of score scales."""
    result = {}
    for channel, ranked in (("lexical", lexical), ("semantic", semantic)):
        seen = set()
        for rank, item in enumerate(ranked, start=1):
            if item.id in seen:
                continue
            seen.add(item.id)
            combined = result.setdefault(item.id, RankedCandidate(mod=item.mod, score=0.0))
            combined.score += 1 / (60 + rank)
            combined.channels.append(channel)
            if channel == "lexical":
                combined.lexical_score = item.lexical_score
                combined.matched_terms = list(item.matched_terms)
            else:
                combined.semantic_score = item.semantic_score
    ranked = sorted(result.values(), key=lambda item: (-item.score, item.id))
    return ranked if limit is None else ranked[:limit]


def normalized_vector(value, *, dimensions: int | None = None) -> list[float] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= 4096:
        return None
    if dimensions is not None and len(value) != dimensions:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        return None
    try:
        values = [float(x) for x in value]
        if any(not math.isfinite(x) or abs(x) > 1e30 for x in values):
            return None
        norm = math.hypot(*values)
        return [x / norm for x in values] if norm > 0 else None
    except (OverflowError, ValueError):
        return None


def rank_semantic(mods, query_vector, vectors: dict[str, list[float]], *, minimum=0.35):
    """Exact cosine scan of already filtered, current records; no ANN claim."""
    query = normalized_vector(query_vector)
    if query is None:
        return []
    result = []
    for mod in mods:
        vector = normalized_vector(vectors.get(mod.id), dimensions=len(query))
        if vector is None:
            continue
        similarity = max(
            -1.0, min(1.0, math.fsum(x * y for x, y in zip(query, vector, strict=True)))
        )
        if similarity < minimum:
            continue
        result.append(
            RankedCandidate(
                mod=mod,
                score=similarity,
                semantic_score=similarity,
                channels=["semantic"],
            )
        )
    return sorted(result, key=lambda item: (-item.score, item.id))
