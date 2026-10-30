import json
from collections import Counter
from pathlib import Path


def test_synthetic_evaluation_dataset_contract():
    path = Path(__file__).resolve().parents[1] / "evaluation" / "incidents_v1.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 24
    assert len({record["id"] for record in records}) == len(records)
    assert Counter(record["expected_severity"] for record in records) == {
        "sev1": 6,
        "sev2": 6,
        "sev3": 6,
        "unknown": 6,
    }
    required = {
        "id",
        "service",
        "summary",
        "expected_severity",
        "expected_symptoms",
        "affected_components",
        "reported_impact",
        "uncertainty",
    }
    for record in records:
        assert set(record) == required
        assert all(record[key] for key in ("id", "service", "summary"))
        assert isinstance(record["expected_symptoms"], list)
        assert isinstance(record["affected_components"], list)
        assert isinstance(record["uncertainty"], list)
