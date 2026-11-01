import importlib.util
import os
import sys
from pathlib import Path

import pytest

EVALUATION_DIR = Path(__file__).resolve().parents[1] / "evaluation"


def load_evaluator():
    path = EVALUATION_DIR / "evaluate_classification.py"
    spec = importlib.util.spec_from_file_location("evaluate_classification", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_holdout_is_distinct_and_balanced():
    evaluator = load_evaluator()
    original = evaluator.load_incidents(EVALUATION_DIR / "incidents_v1.jsonl")
    holdout = evaluator.load_incidents(EVALUATION_DIR / "incidents_holdout_v1.jsonl")
    assert len(holdout) == 8
    assert {record["id"] for record in holdout}.isdisjoint(
        record["id"] for record in original
    )
    assert {record["summary"] for record in holdout}.isdisjoint(
        record["summary"] for record in original
    )
    assert sorted(record["expected_severity"] for record in holdout) == [
        "sev1",
        "sev1",
        "sev2",
        "sev2",
        "sev3",
        "sev3",
        "unknown",
        "unknown",
    ]


def test_evaluator_hides_labels_and_records_quality_fallback_and_latency():
    evaluator = load_evaluator()
    records = [
        {
            "id": "case-1",
            "service": "api",
            "summary": "Complete outage",
            "expected_severity": "sev1",
            "expected_symptoms": ["service_outage"],
            "private_annotator_note": "never send this",
        },
        {
            "id": "case-2",
            "service": "api",
            "summary": "Impact unknown",
            "expected_severity": "unknown",
            "expected_symptoms": [],
        },
    ]

    class FakeModel:
        def __init__(self):
            self.received = []

        def invoke(self, description):
            self.received.append(description)
            if description == "Complete outage":
                return {
                    "recommended_severity": "sev1",
                    "symptoms": ["service_outage"],
                    "rationale": "Complete outage reported.",
                    "affected_components": ["api"],
                    "reported_impact": ["Complete outage"],
                    "source_references": ["Complete outage"],
                    "uncertainties": [],
                }
            return {"recommended_severity": "critical"}

    model = FakeModel()
    result = evaluator.evaluate_dataset(records, model)
    assert result["count"] == 2
    assert result["baseline"]["severity_accuracy"] == 1.0
    assert result["model"]["severity_accuracy"] == 1.0
    assert result["model"]["symptom_precision"] == 1.0
    assert result["model"]["symptom_recall"] == 1.0
    assert result["model"]["structured_output_validity_rate"] == 0.5
    assert result["model"]["fallback_rate"] == 0.5
    assert result["model"]["failure_reasons"] == {"invalid_output": 1}
    assert result["model"]["p95_latency_ms"] >= 0
    assert result["model"]["estimated_cost_usd"] is None
    assert [case["model"]["source"] for case in result["cases"]] == [
        "llm",
        "baseline_fallback",
    ]
    assert all("expected_" not in call for call in model.received)
    assert all("never send this" not in call for call in model.received)


def test_evaluator_estimates_cost_only_from_reported_tokens_and_supplied_rates():
    evaluator = load_evaluator()
    record = {
        "id": "case-1",
        "service": "api",
        "summary": "Complete outage",
        "expected_severity": "sev1",
        "expected_symptoms": [],
    }

    class UsageModel:
        def __init__(self):
            self.token_usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "calls_with_usage": 0,
            }

        def invoke(self, description):
            self.token_usage["input_tokens"] += 1000
            self.token_usage["output_tokens"] += 200
            self.token_usage["calls_with_usage"] += 1
            return {
                "recommended_severity": "unknown",
                "symptoms": [],
                "rationale": "No additional claim.",
                "affected_components": ["api"],
                "reported_impact": [],
                "source_references": [],
                "uncertainties": [],
            }

    result = evaluator.evaluate_dataset(
        [record],
        UsageModel(),
        input_usd_per_million=2,
        output_usd_per_million=8,
    )
    assert result["model"]["reported_token_usage"] == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "calls_with_usage": 1,
    }
    assert result["model"]["estimated_cost_usd"] == pytest.approx(0.0036)


def test_evaluator_rejects_empty_or_colliding_input():
    evaluator = load_evaluator()
    with pytest.raises(ValueError, match="not be empty"):
        evaluator.evaluate_dataset([], object())
    duplicate = {
        "id": "same",
        "service": "api",
        "summary": "Impact unknown",
        "expected_severity": "unknown",
        "expected_symptoms": [],
    }
    with pytest.raises(ValueError, match="unique"):
        evaluator.evaluate_dataset([duplicate, duplicate], object())


def test_explicit_env_file_loads_only_provider_settings(tmp_path, monkeypatch):
    evaluator = load_evaluator()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SENTINEL_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("UNRELATED_SETTING", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "# local only\nOPENAI_API_KEY=test-placeholder\n"
        "SENTINEL_OPENAI_MODEL=gpt-6-luna\nUNRELATED_SETTING=ignored\n",
        encoding="utf-8",
    )
    evaluator.load_env_file(path)
    assert os.getenv("OPENAI_API_KEY") == "test-placeholder"
    assert os.getenv("SENTINEL_OPENAI_MODEL") == "gpt-6-luna"
    assert os.getenv("UNRELATED_SETTING") is None
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("SENTINEL_OPENAI_MODEL")


def test_cli_requires_explicit_live_flag(monkeypatch, capsys):
    evaluator = load_evaluator()
    monkeypatch.setattr(sys, "argv", ["evaluate_classification.py"])
    with pytest.raises(SystemExit):
        evaluator.main()
    assert "pass --live" in capsys.readouterr().err
