"""Offline retrieval regression evaluation on frozen, source-attributed excerpts.

This script does not open the application database, read provider settings, call
models, or seed production records. Timings are opt-in and are not benchmarks of
the HTTP endpoint or a provider. Run from any working directory after installing
the backend package.
"""

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from modsage.catalog import search_terms
from modsage.recommendations import candidate_score, is_eligible, parse_rules
from modsage.retrieval import rank_bm25

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "eval"
FEATURE_FIELDS = ("id", "title", "description_excerpt", "tags", "category", "chinese_support")


@dataclass(frozen=True)
class EvaluationDocument:
    id: str
    title: str
    description: str
    tags: list[str]
    category: str
    chinese_support: str


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    documents: list[EvaluationDocument]
    queries: list[dict]
    corpus_sha256: str
    queries_sha256: str


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def strict_json_loads(content: bytes | str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(content, object_pairs_hook=unique_object)


def feature_hash(record: dict) -> str:
    features = {field: record[field] for field in FEATURE_FIELDS}
    return sha256(
        json.dumps(features, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def validate_records(corpus: dict, queries: dict) -> None:
    """Reject ambiguous IDs, incompatible datasets and invalid judgments."""
    if corpus.get("schema_version") != 1 or queries.get("schema_version") != 1:
        raise ValueError("Unsupported evaluation schema version")
    if not corpus.get("dataset_id") or corpus["dataset_id"] != queries.get("dataset_id"):
        raise ValueError("Corpus/query dataset identities differ")
    if corpus.get("purpose") != "offline_evaluation_only":
        raise ValueError("Corpus must be explicitly evaluation-only")
    documents = corpus.get("documents")
    query_rows = queries.get("queries")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Empty or invalid corpus")
    if not isinstance(query_rows, list) or not query_rows:
        raise ValueError("Empty or invalid queries")
    ids = set()
    for record in documents:
        identifier = record.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", identifier):
            raise ValueError("Invalid Workshop ID")
        if identifier in ids:
            raise ValueError("Duplicate document ID")
        ids.add(identifier)
        if not isinstance(record.get("title"), str) or not record["title"].strip():
            raise ValueError("Missing title")
        if not isinstance(record.get("description_excerpt"), str):
            raise ValueError("Invalid source excerpt")
        if not isinstance(record.get("tags"), list) or not all(
            isinstance(tag, str) for tag in record["tags"]
        ):
            raise ValueError("Invalid tags")
        if record.get("chinese_support") not in {
            "unknown",
            "author_claimed",
            "translation_available",
        }:
            raise ValueError("Invalid Chinese-support state")
        if record.get("source_url") != (
            "https://steamcommunity.com/sharedfiles/filedetails/?id=" + identifier
        ):
            raise ValueError("Source URL must identify the corresponding Workshop item")
        if record.get("feature_sha256") != feature_hash(record):
            raise ValueError("Document feature checksum mismatch")
        if not re.fullmatch(r"[a-f0-9]{64}", record.get("source_content_hash", "")):
            raise ValueError("Missing source content fingerprint")
        if not isinstance(record.get("observed_at"), str) or not record["observed_at"]:
            raise ValueError("Missing source observation time")
    query_ids = set()
    for query in query_rows:
        identifier = query.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in query_ids:
            raise ValueError("Missing or duplicate query ID")
        query_ids.add(identifier)
        if not isinstance(query.get("query"), str) or not query["query"].strip():
            raise ValueError("Empty evaluation query")
        if query.get("split") not in {"development", "regression"}:
            raise ValueError("Unknown evaluation split")
        if query.get("expected") not in {"answerable", "no_answer"}:
            raise ValueError("Unknown answerability label")
        qrels = query.get("qrels")
        if not isinstance(qrels, dict) or any(
            item_id not in ids or type(grade) is not int or grade not in {1, 2}
            for item_id, grade in qrels.items()
        ):
            raise ValueError("Qrels reference missing records or invalid grades")
        if (query["expected"] == "no_answer") != (not qrels):
            raise ValueError("Answerability and qrels disagree")
        forbidden = query.get("forbidden_ids")
        if not isinstance(forbidden, list) or any(item_id not in ids for item_id in forbidden):
            raise ValueError("Forbidden judgment references a missing record")
        if len(forbidden) != len(set(forbidden)):
            raise ValueError("Duplicate forbidden judgment")
        if set(forbidden).intersection(qrels):
            raise ValueError("A record cannot be both relevant and forbidden")
        if not isinstance(query.get("rationale"), str) or not query["rationale"].strip():
            raise ValueError("Missing judgment rationale")


def load_dataset(directory: Path = DEFAULT_DATASET) -> Dataset:
    corpus_bytes = (directory / "catalog.v1.json").read_bytes()
    query_bytes = (directory / "queries.v1.json").read_bytes()
    manifest = strict_json_loads((directory / "manifest.v1.json").read_bytes())
    corpus_digest, query_digest = sha256(corpus_bytes), sha256(query_bytes)
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema version")
    if manifest.get("corpus_sha256") != corpus_digest:
        raise ValueError("Frozen corpus checksum mismatch")
    if manifest.get("queries_sha256") != query_digest:
        raise ValueError("Frozen queries checksum mismatch")
    corpus, queries = strict_json_loads(corpus_bytes), strict_json_loads(query_bytes)
    validate_records(corpus, queries)
    if manifest.get("dataset_id") != corpus["dataset_id"]:
        raise ValueError("Manifest dataset identity differs")
    if manifest.get("documents") != len(corpus["documents"]) or manifest.get("queries") != len(
        queries["queries"]
    ):
        raise ValueError("Manifest record counts differ")
    documents = [
        EvaluationDocument(
            id=record["id"],
            title=record["title"],
            description=record["description_excerpt"],
            tags=record["tags"],
            category=record["category"],
            chinese_support=record["chinese_support"],
        )
        for record in corpus["documents"]
    ]
    return Dataset(corpus["dataset_id"], documents, queries["queries"], corpus_digest, query_digest)


def ranking_metrics(ranked_ids: list[str], qrels: dict[str, int], k: int = 5) -> dict:
    """Positive-query metrics; no-answer rows are scored separately, never as 1s."""
    if k < 1:
        raise ValueError("k must be positive")
    if len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("Duplicate ranked IDs would inflate evaluation metrics")
    if any(type(grade) is not int or grade not in {1, 2} for grade in qrels.values()):
        raise ValueError("Relevant grades must be 1 or 2")
    if not qrels:
        return {"recall_at_k": None, "ndcg_at_k": None, "mrr_at_k": None}
    top = ranked_ids[:k]
    recall = sum(identifier in qrels for identifier in top) / len(qrels)
    dcg = sum(
        (2 ** qrels.get(identifier, 0) - 1) / math.log2(rank + 1)
        for rank, identifier in enumerate(top, start=1)
    )
    ideal = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(sorted(qrels.values(), reverse=True)[:k], start=1)
    )
    reciprocal = next(
        (1 / rank for rank, identifier in enumerate(top, start=1) if identifier in qrels), 0.0
    )
    return {"recall_at_k": recall, "ndcg_at_k": dcg / ideal, "mrr_at_k": reciprocal}


def rank_legacy(eligible: list[EvaluationDocument], query: str, constraints) -> list[str]:
    """Preserve v0.2 weights, soft language preference and descending ID tie break."""
    terms = search_terms(query)
    ranked = []
    for mod in eligible:
        score = candidate_score(mod, terms)
        if score <= 0:
            continue
        if constraints.chinese_preferred and mod.chinese_support in {
            "author_claimed",
            "translation_available",
        }:
            score += 2
        ranked.append((score, mod.id))
    return [identifier for _, identifier in sorted(ranked, reverse=True)]


def evaluate_query(
    documents: list[EvaluationDocument], query: dict, *, k: int = 5, include_timing: bool = False
) -> dict:
    constraints = parse_rules(query["query"])
    # The same bounded input and same hard guards precede both ranking algorithms.
    eligible = [mod for mod in documents if is_eligible(mod, constraints)]
    eligible_ids = {mod.id for mod in eligible}
    result = {
        "id": query["id"],
        "split": query["split"],
        "query": query["query"],
        "expected": query["expected"],
        "qrels": query["qrels"],
        "forbidden_ids": query["forbidden_ids"],
        "constraints": constraints.model_dump(),
        "eligible_count": len(eligible),
        "methods": {},
    }
    for name, ranker in (
        ("legacy_keywords", lambda: rank_legacy(eligible, query["query"], constraints)),
        ("bm25", lambda: [item.id for item in rank_bm25(eligible, query["query"])]),
    ):
        start = perf_counter()
        ranked = ranker()
        elapsed_ms = (perf_counter() - start) * 1000
        if any(identifier not in eligible_ids for identifier in ranked):
            raise ValueError("Ranker returned an unknown or filtered record")
        metrics = ranking_metrics(ranked, query["qrels"], k)
        top = ranked[:k]
        # These judgments were authored independently of is_eligible/ranking.
        forbidden_hits = sorted(set(top).intersection(query["forbidden_ids"]))
        entry = {
            "ids": top,
            "returned_count": len(top),
            "metrics": metrics,
            "forbidden_hits": forbidden_hits,
            "no_answer_correct": not top if query["expected"] == "no_answer" else None,
        }
        if include_timing:
            entry["ranking_ms"] = round(elapsed_ms, 4)
        result["methods"][name] = entry
    return result


def aggregate(rows: list[dict], method: str) -> dict:
    answers = [row["methods"][method] for row in rows if row["expected"] == "answerable"]
    no_answers = [row["methods"][method] for row in rows if row["expected"] == "no_answer"]
    result = {
        "queries": len(rows),
        "answerable_queries": len(answers),
        "no_answer_queries": len(no_answers),
        **{
            metric: (
                sum(row["metrics"][metric] for row in answers) / len(answers) if answers else None
            )
            for metric in ("recall_at_k", "ndcg_at_k", "mrr_at_k")
        },
        "no_answer_accuracy": (
            sum(row["no_answer_correct"] for row in no_answers) / len(no_answers)
            if no_answers
            else None
        ),
        "hard_constraint_violations": sum(
            len(row["methods"][method]["forbidden_hits"]) for row in rows
        ),
        "queries_with_forbidden_judgments": sum(bool(row["forbidden_ids"]) for row in rows),
    }
    timings = [row["methods"][method].get("ranking_ms") for row in rows]
    if timings and all(value is not None for value in timings):
        ordered = sorted(timings)
        result["local_ranking_timing_ms"] = {
            "p50": ordered[math.ceil(0.5 * len(ordered)) - 1],
            "p95": ordered[math.ceil(0.95 * len(ordered)) - 1],
            "total": round(sum(ordered), 4),
        }
    return result


def evaluate_dataset(dataset: Dataset, *, k: int = 5, include_timing: bool = False) -> dict:
    rows = [
        evaluate_query(dataset.documents, query, k=k, include_timing=include_timing)
        for query in dataset.queries
    ]
    summary = {}
    for split in ("all", "development", "regression"):
        selected = rows if split == "all" else [row for row in rows if row["split"] == split]
        summary[split] = {
            method: aggregate(selected, method) for method in ("legacy_keywords", "bm25")
        }
    return {
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "corpus_sha256": dataset.corpus_sha256,
        "queries_sha256": dataset.queries_sha256,
        "documents": len(dataset.documents),
        "k": k,
        "provider_calls": 0,
        "timing_scope": (
            "one local ranking pass; excludes API, database, embeddings and network"
            if include_timing
            else "disabled"
        ),
        "limitations": [
            "Small frozen title/tag/excerpt corpus, not the full live catalog.",
            "Development judgments, not independent human labels or user satisfaction.",
            "Regression split is fixed regression coverage, not an untouched statistical holdout.",
            "Unjudged documents score zero; judgments are scoped to this corpus.",
            "No semantic-provider, hybrid-quality or production-latency claim.",
        ],
        "summary": summary,
        "queries": rows,
    }


def check_report(report: dict) -> list[str]:
    """Gate independently labeled exclusions and no-answer behavior only.

    Quality metrics remain visible, rather than choosing a convenient threshold
    after observing a small development sample.
    """
    failures = []
    for method, metrics in report["summary"]["all"].items():
        if metrics["hard_constraint_violations"]:
            failures.append(f"{method}: independently labeled forbidden result returned")
        if metrics["no_answer_queries"] and metrics["no_answer_accuracy"] != 1.0:
            failures.append(f"{method}: a labeled no-answer query returned candidates")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--k", type=int, choices=range(1, 21), default=5, metavar="1..20")
    parser.add_argument("--output", "--json-output", type=Path)
    parser.add_argument("--timing", action="store_true", help="Include local ranking-only timing")
    parser.add_argument(
        "--check", action="store_true", help="Fail on forbidden/no-answer regressions"
    )
    args = parser.parse_args(argv)
    try:
        report = evaluate_dataset(load_dataset(args.dataset), k=args.k, include_timing=args.timing)
        failures = check_report(report)
        report["checks"] = {"passed": not failures, "failures": failures}
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        print(
            json.dumps(
                {
                    "dataset_id": report["dataset_id"],
                    "documents": report["documents"],
                    "k": args.k,
                    "summary": report["summary"],
                    "checks": report["checks"],
                    "output": str(args.output) if args.output else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if args.check and failures else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
