"""Evaluate the deterministic classifier against the fixed synthetic dataset."""

import json
from pathlib import Path

from sentinel.nodes.classify import classify

DATASET = Path(__file__).with_name("incidents_v1.jsonl")


def load_incidents(path: Path = DATASET) -> list[dict]:
    """Read one labeled incident per JSONL line."""
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def evaluate_dataset(dataset: list[dict]) -> dict:
    """Compute severity accuracy and micro-averaged symptom precision/recall."""
    if not dataset:
        raise ValueError("evaluation dataset must not be empty")

    severity_matches = true_positives = false_positives = false_negatives = 0
    mismatched_ids: list[str] = []

    for record in dataset:
        # Reference labels and annotator notes must never enter classifier input.
        incident = {key: record[key] for key in ("id", "service", "summary")}
        result = classify({"incident": incident})
        expected = set(record["expected_symptoms"])
        predicted = set(result["symptoms"])

        severity_correct = result["severity"] == record["expected_severity"]
        if severity_correct:
            severity_matches += 1
        if not severity_correct or predicted != expected:
            mismatched_ids.append(record["id"])

        true_positives += len(predicted & expected)
        false_positives += len(predicted - expected)
        false_negatives += len(expected - predicted)

    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    return {
        "count": len(dataset),
        "severity_accuracy": severity_matches / len(dataset),
        "symptom_precision": (
            true_positives / precision_denominator if precision_denominator else 0.0
        ),
        "symptom_recall": (
            true_positives / recall_denominator if recall_denominator else 0.0
        ),
        "mismatched_ids": mismatched_ids,
    }


def main() -> None:
    metrics = evaluate_dataset(load_incidents())
    print(f"Incidents: {metrics['count']}")
    print(f"Severity accuracy: {metrics['severity_accuracy']:.3f}")
    print(f"Symptom precision (micro): {metrics['symptom_precision']:.3f}")
    print(f"Symptom recall (micro): {metrics['symptom_recall']:.3f}")
    print("Mismatched IDs: " + (", ".join(metrics["mismatched_ids"]) or "none"))


if __name__ == "__main__":
    main()
