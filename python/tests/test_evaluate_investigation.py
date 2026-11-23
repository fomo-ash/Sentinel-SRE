"""Phase 5 comparison isolation, fixture parity, and failure accounting."""

import json

import pytest

from evaluation.evaluate_investigation import (
    DEV_DATASET,
    FIXTURES,
    HOLDOUT_DATASET,
    FixtureDiagnosticClient,
    _score,
    evaluate_dataset,
    load_cases,
)


class FakeClassifier:
    def __init__(self):
        self.requests = []

    def invoke(self, description):
        self.requests.append(description)
        return {
            "recommended_severity": "unknown",
            "symptoms": [],
            "rationale": "Impact is uncertain.",
            "affected_components": [],
            "reported_impact": [],
            "source_references": [],
            "uncertainties": ["User impact is unknown."],
        }


class FakeDiagnoser:
    def __init__(self, failure=None):
        self.requests = []
        self.failure = failure

    def invoke(self, payload):
        self.requests.append(payload)
        if self.failure is not None:
            raise self.failure
        return {
            "status": "inconclusive",
            "summary": "The current observations do not establish a cause.",
            "hypotheses": [],
            "unresolved_questions": ["Which dependency caused the symptoms?"],
            "historical_context_ids": [],
        }


class FakeRetriever:
    def __init__(self):
        self.queries = []

    def retrieve(self, query, top_k=3):
        self.queries.append((query, top_k))
        return []


def test_dev_and_holdout_labels_are_valid_and_separate():
    dev = load_cases(DEV_DATASET)
    holdout = load_cases(HOLDOUT_DATASET)
    assert len(dev) == len(holdout) == 3
    assert {case["case_id"] for case in dev}.isdisjoint(
        {case["case_id"] for case in holdout}
    )
    assert {case["incident_id"] for case in dev}.isdisjoint(
        {case["incident_id"] for case in holdout}
    )
    client = FixtureDiagnosticClient()
    for case in [*dev, *holdout]:
        assert client.read("health", case["incident_id"], case["service"])
    assert (
        "diag-db-auth-alt"
        in json.loads((FIXTURES / "health_v1.json").read_text())[-1]["incident_id"]
    )


def test_loader_rejects_malformed_and_duplicate_labels(tmp_path):
    valid = load_cases(DEV_DATASET)[0]
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({**valid, "extra_label": "leak"}))
    with pytest.raises(ValueError, match="invalid fields"):
        load_cases(path)
    path.write_text(json.dumps(valid) + "\n" + json.dumps(valid))
    with pytest.raises(ValueError, match="Duplicate case ID"):
        load_cases(path)
    path.write_text(json.dumps({**valid, "expected_evidence_ids": ["hist-one"]}))
    with pytest.raises(ValueError, match="invalid evidence labels"):
        load_cases(path)


def test_three_arms_share_diagnostics_but_hide_labels_from_models():
    records = load_cases(DEV_DATASET)
    classifier = FakeClassifier()
    diagnoser = FakeDiagnoser()
    retriever = FakeRetriever()
    results = evaluate_dataset(
        records, classifier, diagnoser, retriever, FixtureDiagnosticClient()
    )
    assert set(results["arms"]) == {"baseline", "model", "model_history"}
    assert len(classifier.requests) == len(diagnoser.requests) == 6
    assert len(retriever.queries) == 3
    assert all("expected_" not in payload for payload in diagnoser.requests)
    assert all("label_reason" not in payload for payload in diagnoser.requests)
    assert all("expected_" not in query for query in classifier.requests)
    for row in results["cases"]:
        outputs = [row[arm] for arm in ("baseline", "model", "model_history")]
        assert len({item["thread_id"] for item in outputs}) == 3
        assert all(item["tool_call_count"] == 3 for item in outputs)
        assert all(
            item["current_evidence_ids"] == outputs[0]["current_evidence_ids"]
            for item in outputs
        )
        assert all(item["citations_valid"] for item in outputs)


def test_provider_failure_is_counted_and_cannot_assert_a_cause():
    records = [load_cases(DEV_DATASET)[0]]
    results = evaluate_dataset(
        records,
        FakeClassifier(),
        FakeDiagnoser(RuntimeError("synthetic provider failure")),
        FakeRetriever(),
        FixtureDiagnosticClient(),
    )
    for arm in ("model", "model_history"):
        output = results["cases"][0][arm]
        assert output["diagnosis_status"] == "inconclusive"
        assert output["diagnosis_source"] == "fallback"
        assert output["diagnosis_model_calls"] == 1
        assert results["arms"][arm]["failure_reasons"] == {"provider_error": 1}
        assert results["arms"][arm]["fallback_rate"] == 1.0


def test_metric_calculation_counts_supported_miss_and_correct_abstention():
    cases = load_cases(DEV_DATASET)[:2]
    outputs = [
        {
            "final_severity": "unknown",
            "symptoms": ["database_connectivity"],
            "diagnosis_status": "inconclusive",
            "supporting_evidence_ids": [],
            "citations_valid": True,
            "diagnosis_source": "fallback",
            "classification_source": "baseline",
            "diagnosis_repair_attempted": True,
            "diagnosis_failure_reason": "invalid_output",
            "total_latency_ms": 10.0,
            "tool_call_count": 3,
            "diagnosis_model_calls": 2,
        },
        {
            "final_severity": "sev2",
            "symptoms": ["high_latency"],
            "diagnosis_status": "inconclusive",
            "supporting_evidence_ids": [],
            "citations_valid": True,
            "diagnosis_source": "llm",
            "classification_source": "llm",
            "diagnosis_repair_attempted": False,
            "diagnosis_failure_reason": None,
            "total_latency_ms": 20.0,
            "tool_call_count": 3,
            "diagnosis_model_calls": 1,
        },
    ]
    metrics = _score(cases, outputs)
    assert metrics["severity_accuracy"] == 1.0
    assert metrics["diagnosis_status_accuracy"] == 0.5
    assert metrics["supported_cause_recall"] == 0.0
    assert metrics["correct_abstention_rate"] == 1.0
    assert metrics["fallback_rate"] == metrics["repair_rate"] == 0.5
    assert metrics["mean_diagnostic_calls"] == 3
    assert metrics["p95_total_latency_ms"] == 20.0
