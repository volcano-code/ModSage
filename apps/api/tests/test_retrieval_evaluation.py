"""Independent metric and fixture-integrity checks for the offline evaluation."""

import copy
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "modsage_retrieval_evaluation", ROOT / "scripts/evaluate_retrieval.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluation
SPEC.loader.exec_module(evaluation)


def test_graded_metrics_match_hand_calculated_values():
    metrics = evaluation.ranking_metrics(
        ["irrelevant", "b", "a", "c"], {"a": 2, "b": 1, "c": 1}, k=3
    )
    expected_dcg = 1 / math.log2(3) + 3 / math.log2(4)
    expected_ideal = 3 + 1 / math.log2(3) + 1 / math.log2(4)
    assert metrics["recall_at_k"] == pytest.approx(2 / 3)
    assert metrics["ndcg_at_k"] == pytest.approx(expected_dcg / expected_ideal)
    assert metrics["mrr_at_k"] == 0.5


def test_no_answer_queries_do_not_inflate_positive_retrieval_metrics():
    assert evaluation.ranking_metrics([], {}) == {
        "recall_at_k": None,
        "ndcg_at_k": None,
        "mrr_at_k": None,
    }
    assert evaluation.ranking_metrics(["wrong"], {})["recall_at_k"] is None
    assert evaluation.ranking_metrics([], {"relevant": 2}) == {
        "recall_at_k": 0.0,
        "ndcg_at_k": 0.0,
        "mrr_at_k": 0.0,
    }


def test_duplicate_results_and_duplicate_json_qrels_are_rejected():
    with pytest.raises(ValueError, match="Duplicate ranked"):
        evaluation.ranking_metrics(["a", "a"], {"a": 2})
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        evaluation.strict_json_loads('{"qrels":{"10001":2,"10001":1}}')


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_qrel",
        "duplicate_query",
        "duplicate_document",
        "contradiction",
        "bad_no_answer",
        "changed_feature",
    ],
)
def test_dataset_validation_rejects_ambiguous_or_broken_labels(mutation):
    corpus = json.loads((ROOT / "eval/catalog.v1.json").read_bytes())
    queries = json.loads((ROOT / "eval/queries.v1.json").read_bytes())
    query = queries["queries"][0]
    if mutation == "missing_qrel":
        query["qrels"]["9999999999999999999"] = 2
    elif mutation == "duplicate_query":
        queries["queries"].append(copy.deepcopy(query))
    elif mutation == "duplicate_document":
        corpus["documents"].append(copy.deepcopy(corpus["documents"][0]))
    elif mutation == "contradiction":
        query["forbidden_ids"].append(next(iter(query["qrels"])))
    elif mutation == "bad_no_answer":
        query["expected"] = "no_answer"
    else:
        corpus["documents"][0]["description_excerpt"] += " modified"
    with pytest.raises(ValueError):
        evaluation.validate_records(corpus, queries)


def test_frozen_file_checksum_detects_a_modified_corpus(tmp_path):
    for filename in ("catalog.v1.json", "queries.v1.json", "manifest.v1.json"):
        (tmp_path / filename).write_bytes((ROOT / "eval" / filename).read_bytes())
    with (tmp_path / "catalog.v1.json").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="Frozen corpus checksum mismatch"):
        evaluation.load_dataset(tmp_path)


def test_both_rankers_receive_identical_hard_filtered_candidates(monkeypatch):
    documents = [
        evaluation.EvaluationDocument(
            "10001", "TEST Empire reskin", "", ["graphical"], "visual", "unknown"
        ),
        evaluation.EvaluationDocument(
            "10002", "TEST Empire units", "Adds Empire units", ["units"], "units", "unknown"
        ),
    ]
    seen = []

    def legacy(mods, query, constraints):
        seen.append([mod.id for mod in mods])
        return [mod.id for mod in mods]

    def bm25(mods, query):
        seen.append([mod.id for mod in mods])
        return [SimpleNamespace(id=mod.id) for mod in mods]

    monkeypatch.setattr(evaluation, "rank_legacy", legacy)
    monkeypatch.setattr(evaluation, "rank_bm25", bm25)
    query = {
        "id": "test",
        "split": "development",
        "query": "帝国，兵种保持原版，只改外观",
        "expected": "answerable",
        "qrels": {"10001": 2},
        "forbidden_ids": ["10002"],
    }
    result = evaluation.evaluate_query(documents, query)
    assert seen == [["10001"], ["10001"]]
    assert all(row["ids"] == ["10001"] for row in result["methods"].values())


def test_independent_forbidden_labels_are_measured_even_when_rule_guard_allows_result():
    document = evaluation.EvaluationDocument(
        "10001", "TEST Empire reskin", "", ["graphical"], "visual", "unknown"
    )
    query = {
        "id": "test",
        "split": "development",
        "query": "Empire reskin",
        "expected": "no_answer",
        "qrels": {},
        "forbidden_ids": ["10001"],
    }
    row = evaluation.evaluate_query([document], query)
    for method in ("legacy_keywords", "bm25"):
        metrics = evaluation.aggregate([row], method)
        assert metrics["hard_constraint_violations"] == 1
        assert metrics["no_answer_accuracy"] == 0
        assert metrics["recall_at_k"] is None
    report = {
        "summary": {
            "all": {
                method: evaluation.aggregate([row], method)
                for method in ("legacy_keywords", "bm25")
            }
        }
    }
    assert len(evaluation.check_report(report)) == 4


def test_frozen_evaluation_is_deterministic_and_never_calls_model_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline evaluation must not create providers or databases")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    monkeypatch.setattr("modsage.database.make_database", forbidden)
    dataset = evaluation.load_dataset()
    first = evaluation.evaluate_dataset(dataset)
    second = evaluation.evaluate_dataset(dataset)
    assert first == second
    assert first["provider_calls"] == 0
    assert first["documents"] == 42
    assert len(first["queries"]) == 28
    assert first["timing_scope"] == "disabled"
    assert not evaluation.check_report(first)
