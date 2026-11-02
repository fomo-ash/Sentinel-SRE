"""Fixed-query retrieval metrics and label validation."""

import json
from pathlib import Path

import pytest

from evaluation.evaluate_retrieval import (
    DEFAULT_DATASET,
    evaluate_retrieval,
    load_queries,
)
from retrieval.history import load_history


def test_fixed_queries_have_known_labels_and_distinct_wording() -> None:
    history = load_history()
    queries = load_queries(DEFAULT_DATASET, {row["id"] for row in history})

    assert len(queries) == 21
    assert len({row["id"] for row in queries}) == len(queries)
    assert not ({row["query"] for row in queries} & {row["summary"] for row in history})
    assert all(len(row["relevant_ids"]) == 1 for row in queries)


def test_metrics_count_rank_and_recall() -> None:
    queries = [
        {"id": "q1", "query": "first", "relevant_ids": ["A"]},
        {"id": "q2", "query": "second", "relevant_ids": ["C"]},
    ]

    class FakeRetriever:
        def retrieve(self, query, top_k=3):
            ids = ["B", "A", "C"] if query == "first" else ["C", "B", "A"]
            return [{"id": item, "score": 1.0} for item in ids[:top_k]]

    result = evaluate_retrieval(FakeRetriever(), queries, ks=(1, 3))

    assert result["query_count"] == 2
    assert result["failure_count"] == 0
    assert result["metrics"]["1"] == {"recall_at_k": 0.5, "mrr_at_k": 0.5}
    assert result["metrics"]["3"] == {"recall_at_k": 1.0, "mrr_at_k": 0.75}


def test_retrieval_error_is_counted_as_a_miss() -> None:
    class FailingRetriever:
        def retrieve(self, query, top_k=3):
            raise TimeoutError("embedding backend unavailable")

    result = evaluate_retrieval(
        FailingRetriever(),
        [{"id": "q1", "query": "first", "relevant_ids": ["A"]}],
    )
    assert result["failure_count"] == 1
    assert result["metrics"]["3"]["recall_at_k"] == 0.0
    assert result["queries"][0]["error"] == "TimeoutError"


def test_query_loader_rejects_labels_outside_history(tmp_path: Path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "q1",
                "query": "a valid query",
                "relevant_ids": ["eval-001"],
                "label_reason": "This is an evaluation label, not history.",
            }
        )
    )

    with pytest.raises(ValueError, match="invalid relevant_ids"):
        load_queries(path, {"hist-001"})
