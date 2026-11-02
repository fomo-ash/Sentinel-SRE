"""Deterministic ranking tests for historical context retrieval."""

from copy import deepcopy

import pytest

from retrieval.history import load_history
from retrieval.retriever import HistoricalIncidentRetriever


class FakeEncoder:
    embedding_dim = 2

    def __init__(self, vectors):
        self.vectors = vectors
        self.queries = {"similar incident": [1.0, 0.0]}
        self.document_calls = []

    def encode_documents(self, texts):
        self.document_calls.append(texts)
        return [
            next(vector for summary, vector in self.vectors.items() if summary in text)
            for text in texts
        ]

    def encode_query(self, query):
        return self.queries[query]


def test_retrieval_ranks_history_and_keeps_provenance() -> None:
    records = load_history()[:3]
    vectors = {
        records[0]["summary"]: [1.0, 0.0],
        records[1]["summary"]: [0.0, 1.0],
        records[2]["summary"]: [0.6, 0.8],
    }
    encoder = FakeEncoder(vectors)
    retriever = HistoricalIncidentRetriever(records, embedding_model=encoder)

    matches = retriever.retrieve("similar incident", top_k=2)

    assert [match["id"] for match in matches] == ["hist-001", "hist-003"]
    assert matches[0]["score"] == pytest.approx(1.0)
    assert matches[1]["score"] == pytest.approx(0.6)
    assert matches[0]["historical_incident"]["provenance"]["type"] == "synthetic"
    assert len(encoder.document_calls) == 1
    assert all(
        record["summary"] in text and record["impact"] in text
        for record, text in zip(records, encoder.document_calls[0])
    )

    # Callers cannot change the indexed history through the input or results.
    records[0]["summary"] = "changed"
    matches[0]["historical_incident"]["summary"] = "changed again"
    assert (
        retriever.retrieve("similar incident", 1)[0]["historical_incident"]["summary"]
        != "changed again"
    )


def test_tied_scores_sort_by_stable_id() -> None:
    records = load_history()[1:3]
    records.reverse()
    vectors = {record["summary"]: [1.0, 0.0] for record in records}
    retriever = HistoricalIncidentRetriever(
        records, embedding_model=FakeEncoder(vectors)
    )

    assert [match["id"] for match in retriever.retrieve("similar incident", 9)] == [
        "hist-002",
        "hist-003",
    ]


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_invalid_top_k_is_rejected(top_k) -> None:
    records = load_history()[:1]
    encoder = FakeEncoder({records[0]["summary"]: [1.0, 0.0]})
    retriever = HistoricalIncidentRetriever(records, embedding_model=encoder)

    with pytest.raises(ValueError, match="top_k"):
        retriever.retrieve("similar incident", top_k)


def test_bad_query_or_vectors_fail_before_ranking() -> None:
    records = load_history()[:1]
    encoder = FakeEncoder({records[0]["summary"]: [1.0, 0.0]})
    retriever = HistoricalIncidentRetriever(records, embedding_model=encoder)
    with pytest.raises(ValueError, match="Query"):
        retriever.retrieve(" ")

    encoder.queries["similar incident"] = [0.0, 0.0]
    with pytest.raises(ValueError, match="zero"):
        retriever.retrieve("similar incident")

    encoder.vectors[records[0]["summary"]] = [1.0]
    with pytest.raises(ValueError, match="dimension"):
        HistoricalIncidentRetriever(records, embedding_model=encoder)


def test_duplicate_history_is_rejected_before_embedding() -> None:
    record = load_history()[0]
    encoder = FakeEncoder({record["summary"]: [1.0, 0.0]})
    with pytest.raises(ValueError, match="Duplicate history id"):
        HistoricalIncidentRetriever(
            [deepcopy(record), deepcopy(record)], embedding_model=encoder
        )
    assert encoder.document_calls == []
