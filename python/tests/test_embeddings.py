"""Embedding wrapper behavior without model downloads or network calls."""

import importlib.util
import math
from pathlib import Path

import pytest


def load_embeddings_module():
    path = Path(__file__).resolve().parents[1] / "retrieval" / "embeddings.py"
    spec = importlib.util.spec_from_file_location("embeddings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EmbeddingModel = load_embeddings_module().EmbeddingModel


class FakeModel:
    def __init__(self):
        self.calls = []
        self.result = None

    def get_embedding_dimension(self):
        return 2

    def encode(self, value, **kwargs):
        self.calls.append((value, kwargs))
        if self.result is not None:
            return self.result
        if isinstance(value, list):
            return [[0.6, 0.8] for _ in value]
        return [0.6, 0.8]


def test_encodes_documents_and_query_with_normalization() -> None:
    fake = FakeModel()
    model = EmbeddingModel(model=fake)

    assert model.embedding_dim == 2
    assert model.encode_documents(["first", "second"]) == [[0.6, 0.8]] * 2
    assert model.encode_query("incident") == [0.6, 0.8]
    assert fake.calls == [
        (
            ["first", "second"],
            {"normalize_embeddings": True, "convert_to_numpy": True},
        ),
        ("incident", {"normalize_embeddings": True, "convert_to_numpy": True}),
    ]


def test_empty_batch_never_calls_model() -> None:
    fake = FakeModel()
    assert EmbeddingModel(model=fake).encode_documents([]) == []
    assert fake.calls == []


def test_legacy_dimension_method_is_supported() -> None:
    fake = FakeModel()
    fake.get_sentence_embedding_dimension = lambda: 2
    fake.get_embedding_dimension = None
    assert EmbeddingModel(model=fake).embedding_dim == 2


@pytest.mark.parametrize("value", ["", "  ", None])
def test_empty_query_is_rejected(value) -> None:
    with pytest.raises(ValueError, match="Query"):
        EmbeddingModel(model=FakeModel()).encode_query(value)


def test_invalid_document_and_output_shapes_are_rejected() -> None:
    fake = FakeModel()
    model = EmbeddingModel(model=fake)
    with pytest.raises(ValueError, match="Documents"):
        model.encode_documents(["valid", ""])

    fake.result = [[0.6, 0.8]]
    with pytest.raises(ValueError, match="number of documents"):
        model.encode_documents(["first", "second"])

    fake.result = [0.6]
    with pytest.raises(ValueError, match="wrong dimension"):
        model.encode_query("incident")


def test_non_finite_output_is_rejected() -> None:
    fake = FakeModel()
    fake.result = [math.nan, 0.8]
    with pytest.raises(ValueError, match="non-finite"):
        EmbeddingModel(model=fake).encode_query("incident")
