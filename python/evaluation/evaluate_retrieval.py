"""Measure in-memory history retrieval on fixed synthetic queries."""

import argparse
import hashlib
import importlib.metadata
import json
import time
from pathlib import Path
from typing import Protocol

from retrieval.embeddings import EmbeddingModel
from retrieval.history import load_history
from retrieval.retriever import HistoricalIncidentRetriever, HistoricalMatch

DEFAULT_DATASET = Path(__file__).with_name("retrieval_queries_v1.jsonl")


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 3) -> list[HistoricalMatch]: ...


def load_queries(path: Path, history_ids: set[str]) -> list[dict]:
    """Validate query labels against the history corpus before evaluation."""
    queries = []
    seen_ids = set()
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            query_id = row.get("id")
            query = row.get("query")
            relevant = row.get("relevant_ids")
            reason = row.get("label_reason")
            if not isinstance(query_id, str) or not query_id.strip():
                raise ValueError(f"{path}:{line_number}: invalid query id")
            if query_id in seen_ids:
                raise ValueError(f"{path}:{line_number}: duplicate query id")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"{path}:{line_number}: invalid query")
            if (
                not isinstance(relevant, list)
                or not relevant
                or any(not isinstance(item, str) for item in relevant)
                or len(relevant) != len(set(relevant))
                or not set(relevant) <= history_ids
            ):
                raise ValueError(f"{path}:{line_number}: invalid relevant_ids")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{path}:{line_number}: missing label_reason")
            seen_ids.add(query_id)
            queries.append(row)
    if not queries:
        raise ValueError("Retrieval query dataset must not be empty")
    return queries


def evaluate_retrieval(
    retriever: Retriever,
    queries: list[dict],
    *,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """Report Recall@k and MRR@k from one retrieval call per query."""
    if not ks or any(
        isinstance(k, bool) or not isinstance(k, int) or k < 1 for k in ks
    ):
        raise ValueError("ks must contain positive integers")
    ks = tuple(sorted(set(ks)))
    results = []

    for row in queries:
        started = time.perf_counter()
        error = None
        try:
            matches = retriever.retrieve(row["query"], top_k=ks[-1])
            ranked_ids = [match["id"] for match in matches]
            if len(ranked_ids) != len(set(ranked_ids)):
                raise ValueError("Retriever returned duplicate IDs")
        except Exception as exc:
            matches = []
            ranked_ids = []
            error = type(exc).__name__
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(
            {
                "id": row["id"],
                "relevant_ids": row["relevant_ids"],
                "ranked_ids": ranked_ids,
                "scores": [match["score"] for match in matches],
                "latency_ms": latency_ms,
                "error": error,
            }
        )

    metrics = {}
    for k in ks:
        recalls = []
        reciprocals = []
        for result in results:
            relevant = set(result["relevant_ids"])
            top_ids = result["ranked_ids"][:k]
            recalls.append(len(relevant.intersection(top_ids)) / len(relevant))
            first_rank = next(
                (
                    rank
                    for rank, item in enumerate(top_ids, start=1)
                    if item in relevant
                ),
                None,
            )
            reciprocals.append(1 / first_rank if first_rank else 0.0)
        metrics[str(k)] = {
            "recall_at_k": sum(recalls) / len(recalls),
            "mrr_at_k": sum(reciprocals) / len(reciprocals),
        }

    return {
        "query_count": len(queries),
        "failure_count": sum(result["error"] is not None for result in results),
        "mean_latency_ms": sum(result["latency_ms"] for result in results)
        / len(results),
        "metrics": metrics,
        "queries": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument(
        "--model-id",
        help="Stable model name to record when --model is a local directory",
    )
    args = parser.parse_args()

    history = load_history()
    queries = load_queries(args.dataset, {row["id"] for row in history})
    index_started = time.perf_counter()
    embedding = EmbeddingModel(model_name=args.model)
    retriever = HistoricalIncidentRetriever(history, embedding_model=embedding)
    index_build_ms = (time.perf_counter() - index_started) * 1000
    result = evaluate_retrieval(retriever, queries)
    result["dataset"] = args.dataset.name
    result["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    result["history_count"] = len(history)
    history_path = (
        Path(__file__).resolve().parents[1] / "history" / "incidents_v1.jsonl"
    )
    result["history_sha256"] = hashlib.sha256(history_path.read_bytes()).hexdigest()
    result["model"] = args.model_id or args.model
    weights_path = Path(args.model) / "model.safetensors"
    result["model_weights_sha256"] = (
        hashlib.sha256(weights_path.read_bytes()).hexdigest()
        if weights_path.is_file()
        else None
    )
    result["sentence_transformers_version"] = importlib.metadata.version(
        "sentence-transformers"
    )
    result["embedding_dimension"] = embedding.embedding_dim
    result["index_build_ms"] = index_build_ms

    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
