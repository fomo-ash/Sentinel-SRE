"""Local sentence embeddings for the separate synthetic history corpus."""

import math
from typing import Any


class EmbeddingModel:
    """Wrap a sentence-transformer model with a small, validated API."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        *,
        model: Any | None = None,
    ) -> None:
        if model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Install the retrieval extra before loading an embedding model"
                ) from exc
            model = SentenceTransformer(model_name)

        self.model = model
        # Recent releases renamed this method; support either installed API.
        get_dimension = getattr(model, "get_embedding_dimension", None)
        if get_dimension is None:
            get_dimension = getattr(model, "get_sentence_embedding_dimension", None)
        if not callable(get_dimension):
            raise ValueError("Embedding model must report its dimension")

        dimension = get_dimension()
        if (
            isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or dimension < 1
        ):
            raise ValueError("Embedding dimension must be a positive integer")
        self.embedding_dim = dimension

    def _vector(self, value: Any) -> list[float]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if not isinstance(value, list) or len(value) != self.embedding_dim:
            raise ValueError(
                "Embedding model returned a vector with the wrong dimension"
            )

        vector: list[float] = []
        for item in value:
            if isinstance(item, bool):
                raise ValueError("Embedding model returned a non-numeric value")
            try:
                number = float(item)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Embedding model returned a non-numeric value"
                ) from exc
            if not math.isfinite(number):
                raise ValueError("Embedding model returned a non-finite value")
            vector.append(number)
        return vector

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed historical summaries in input order, with unit normalization."""
        if not isinstance(texts, list) or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise ValueError("Documents must be a list of non-empty strings")
        if not texts:
            return []

        embeddings = self.model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ValueError("Embedding model returned the wrong number of documents")
        return [self._vector(embedding) for embedding in embeddings]

    def encode_query(self, query: str) -> list[float]:
        """Embed one incoming incident description with unit normalization."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string")

        embedding = self.model.encode(
            query,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return self._vector(embedding)
