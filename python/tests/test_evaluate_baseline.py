import importlib.util
from pathlib import Path

import pytest


def load_evaluator():
    path = Path(__file__).resolve().parents[1] / "evaluation" / "evaluate_baseline.py"
    spec = importlib.util.spec_from_file_location("evaluate_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metrics_and_label_separation(monkeypatch):
    evaluator = load_evaluator()
    seen = []

    def fake_classify(state):
        seen.append(state)
        return {"severity": "sev1", "symptoms": ["high_latency", "elevated_errors"]}

    monkeypatch.setattr(evaluator, "classify", fake_classify)
    records = [
        {
            "id": "a",
            "service": "api",
            "summary": "outage",
            "expected_severity": "sev1",
            "expected_symptoms": ["high_latency"],
        },
        {
            "id": "b",
            "service": "api",
            "summary": "isolated",
            "expected_severity": "sev3",
            "expected_symptoms": ["elevated_errors", "network_connectivity"],
        },
    ]
    result = evaluator.evaluate_dataset(records)
    assert result == {
        "count": 2,
        "severity_accuracy": 0.5,
        "symptom_precision": 0.5,
        "symptom_recall": pytest.approx(2 / 3),
        "mismatched_ids": ["a", "b"],
    }
    assert seen == [
        {"incident": {"id": "a", "service": "api", "summary": "outage"}},
        {"incident": {"id": "b", "service": "api", "summary": "isolated"}},
    ]


def test_empty_dataset_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        load_evaluator().evaluate_dataset([])
