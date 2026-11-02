"""Rank synthetic historical incidents by cosine similarity."""

import math
from copy import deepcopy
from typing import Protocol, TypedDict

from .embeddings import EmbeddingModel
from .history import load_history, validate_history


class EmbeddingEncoder(Protocol):
    """Minimal embedding contract used by the in-memory index."""

    embedding_dim: int

    def encode_documents(self, texts: list[str]) -> list[list[float]]: ...

    def encode_query(self, query: str) -> list[float]: ...


class HistoricalMatch(TypedDict):
    """A scored past case, explicitly separate from current evidence."""

    id: str
    score: float
    historical_incident: dict[str, object]


class HistoricalIncidentRetriever:
    """Embed history once and return scored past cases for a query."""

    def __init__(
        self,
        historical_incidents: list[dict[str, object]] | None = None,
        *,
        embedding_model: EmbeddingEncoder | None = None,
    ) -> None:
        records = (
            load_history() if historical_incidents is None else historical_incidents
        )
        ids, _ = validate_history(records)
        self._records = deepcopy(records)
        self._ids = ids
        self._encoder = EmbeddingModel() if embedding_model is None else embedding_model

        # Match on reported symptoms and impact; a past cause is not known yet.
        search_texts = [
            (
                f"Service: {record['service']}\n"
                f"Incident: {record['summary']}\n"
                f"Symptoms: {', '.join(record['symptoms'])}\n"
                f"Impact: {record['impact']}"
            )
            for record in records
        ]
        vectors = self._encoder.encode_documents(search_texts)
        if not isinstance(vectors, list) or len(vectors) != len(records):
            raise ValueError("Embedding model returned the wrong number of documents")
        self._vectors = [self._unit_vector(vector) for vector in vectors]

    def _unit_vector(self, vector: list[float]) -> tuple[float, ...]:
        dimension = self._encoder.embedding_dim
        if (
            not isinstance(dimension, int)
            or isinstance(dimension, bool)
            or dimension < 1
            or not isinstance(vector, list)
            or len(vector) != dimension
        ):
            raise ValueError("Embedding model returned an invalid vector dimension")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError(
                "Embedding model returned a non-finite or non-numeric vector"
            )

        magnitude = math.sqrt(math.fsum(value * value for value in vector))
        if magnitude == 0 or not math.isfinite(magnitude):
            raise ValueError("Embedding model returned a zero or invalid vector")
        return tuple(value / magnitude for value in vector)

    def retrieve(self, query: str, top_k: int = 3) -> list[HistoricalMatch]:
        """Return highest-scoring history, with stable ID ties and provenance."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")

        query_vector = self._unit_vector(self._encoder.encode_query(query))
        ranked = sorted(
            (
                (math.fsum(a * b for a, b in zip(query_vector, vector)), index)
                for index, vector in enumerate(self._vectors)
            ),
            key=lambda item: (-item[0], self._ids[item[1]]),
        )

        return [
            {
                "id": self._ids[index],
                "score": max(-1.0, min(1.0, score)),
                "historical_incident": deepcopy(self._records[index]),
            }
            for score, index in ranked[:top_k]
        ]
