from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from sentinel.schemas import (
    InvestigationState,
    classifySchema,
    validate_diagnosis,
    validate_incident,
)


def test_incident_requires_nonempty_fields():
    with pytest.raises(ValueError, match="incident.summary"):
        validate_incident({"id": "inc-1", "service": "api", "summary": "  "})


def test_classification_schema_accepts_valid_output():
    result = {
        "recommended_severity": "sev2",
        "symptoms": ["high_latency"],
        "confidence": 0.8,
        "rationale": "Some requests timed out.",
        "affected_components": ["api"],
        "reported_impact": ["Some requests failed."],
        "source_references": [],
        "uncertainties": ["Affected-user count is unknown."],
    }
    assert TypeAdapter(classifySchema).validate_python(result) == result


def test_classification_schema_rejects_unrecognized_symptom():
    result = {
        "recommended_severity": "unknown",
        "symptoms": ["root_cause_guess"],
        "rationale": "Impact is unclear.",
        "affected_components": [],
        "reported_impact": [],
        "source_references": [],
        "uncertainties": ["User impact is unknown."],
    }
    with pytest.raises(ValidationError, match="symptoms"):
        TypeAdapter(classifySchema).validate_python(result)


def test_classification_schema_requires_correct_uncertainty_field():
    result = {
        "recommended_severity": "unknown",
        "symptoms": [],
        "rationale": "Impact is unclear.",
        "affected_components": [],
        "reported_impact": [],
        "source_references": [],
        "uncertainities": ["User impact is unknown."],
    }
    with pytest.raises(ValidationError, match="uncertainties"):
        TypeAdapter(classifySchema).validate_python(result)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_classification_schema_rejects_out_of_range_confidence(confidence):
    result = {
        "recommended_severity": "unknown",
        "symptoms": [],
        "confidence": confidence,
        "rationale": "Impact is unclear.",
        "affected_components": [],
        "reported_impact": [],
        "source_references": [],
        "uncertainties": ["User impact is unknown."],
    }
    with pytest.raises(ValidationError, match="confidence"):
        TypeAdapter(classifySchema).validate_python(result)


def test_graph_state_keeps_model_interpretation_separate_from_policy_severity():
    interpretation = {
        "recommended_severity": "sev1",
        "symptoms": [],
        "rationale": "The report describes an outage.",
        "affected_components": ["api"],
        "reported_impact": ["All users are affected."],
        "source_references": ["All users are affected"],
        "uncertainties": [],
    }
    state = {
        "incident": {"id": "inc-1", "service": "api", "summary": "Outage"},
        "model_interpretation": interpretation,
        "classification_source": "llm",
        "failure_reason": None,
        "final_severity": "unknown",
    }
    assert TypeAdapter(InvestigationState).validate_python(state) == state


def test_graph_state_rejects_unrecognized_failure_source_and_provider_object():
    state = {
        "incident": {"id": "inc-1", "service": "api", "summary": "Outage"},
        "classification_source": "maybe",
        "failure_reason": "other",
        "model_interpretation": object(),
    }
    with pytest.raises(ValidationError) as error:
        TypeAdapter(InvestigationState).validate_python(state)
    for field in ("classification_source", "failure_reason", "model_interpretation"):
        assert field in str(error.value)


def _diagnosis_state():
    return {
        "incident": {"id": "diag-one", "service": "api", "summary": "API errors"},
        "evidence": [
            {
                "id": "diag-one:health:ready",
                "incident_id": "diag-one",
                "service": "api",
                "kind": "health",
                "source_ref": "GET /incidents/diag-one/health#checks/ready",
                "observed_at": "2026-01-01T00:00:00Z",
                "observation": "unhealthy: readiness failed",
            },
            {
                "id": "diag-one:metric:errors",
                "incident_id": "diag-one",
                "service": "api",
                "kind": "metric",
                "source_ref": "GET /incidents/diag-one/metrics#metrics/errors",
                "observed_at": "2026-01-01T00:00:00Z",
                "observation": "errors=0.12 ratio over 5m",
            },
            {
                "id": "diag-other:log:failure",
                "incident_id": "diag-other",
                "service": "api",
                "kind": "log",
                "source_ref": "GET /incidents/diag-other/logs#entries/failure",
                "observed_at": "2026-01-01T00:00:00Z",
                "observation": "A different incident failed",
            },
        ],
        "historical_matches": [{"id": "hist-one"}],
        "diagnostic_failures": [
            {"kind": "logs", "category": "timeout", "attempts": 1, "latency_ms": 2.0}
        ],
    }


def _supported_diagnosis():
    return {
        "status": "supported",
        "summary": "Current API observations support a failure hypothesis.",
        "hypotheses": [
            {
                "cause": "The API readiness check failed.",
                "supporting_evidence_ids": [
                    "diag-one:health:ready",
                    "diag-one:metric:errors",
                ],
                "contradicting_evidence_ids": [],
            }
        ],
        "unresolved_questions": ["Why did the readiness check fail?"],
        "historical_context_ids": ["hist-one"],
        "source": "deterministic",
    }


def test_diagnosis_accepts_current_evidence_and_separate_history_context():
    value = _supported_diagnosis()
    assert validate_diagnosis(value, _diagnosis_state()) == value


def test_inconclusive_diagnosis_can_abstain_without_evidence():
    value = {
        "status": "inconclusive",
        "summary": "Current observations do not establish a cause.",
        "hypotheses": [],
        "unresolved_questions": ["What failed?"],
        "historical_context_ids": [],
        "source": "deterministic",
    }
    state = _diagnosis_state()
    state["evidence"] = []
    assert validate_diagnosis(value, state) == value


@pytest.mark.parametrize(
    "citation",
    [
        "unknown-id",
        "hist-one",
        "diag-other:log:failure",
        "timeout",
    ],
)
def test_diagnosis_rejects_non_current_supporting_citation(citation):
    value = _supported_diagnosis()
    value["hypotheses"][0]["supporting_evidence_ids"] = [citation]
    with pytest.raises(ValueError, match="non-current evidence"):
        validate_diagnosis(value, _diagnosis_state())


def test_diagnosis_rejects_cross_service_and_contradicting_citations():
    state = _diagnosis_state()
    state["evidence"][1]["service"] = "worker"
    value = _supported_diagnosis()
    with pytest.raises(ValueError, match="non-current evidence"):
        validate_diagnosis(value, state)
    value = _supported_diagnosis()
    value["hypotheses"][0]["contradicting_evidence_ids"] = ["hist-one"]
    with pytest.raises(ValueError, match="non-current evidence"):
        validate_diagnosis(value, _diagnosis_state())


def test_diagnosis_rejects_unknown_history_and_unsupported_cause():
    value = _supported_diagnosis()
    value["historical_context_ids"] = ["hist-unknown"]
    with pytest.raises(ValueError, match="Unknown historical"):
        validate_diagnosis(value, _diagnosis_state())
    value = _supported_diagnosis()
    value["hypotheses"][0]["supporting_evidence_ids"] = []
    with pytest.raises(ValueError, match="needs current evidence"):
        validate_diagnosis(value, _diagnosis_state())
    value["hypotheses"] = []
    with pytest.raises(ValueError, match="needs a hypothesis"):
        validate_diagnosis(value, _diagnosis_state())


def test_diagnosis_rejects_bad_shape_and_duplicate_current_evidence_ids():
    value = _supported_diagnosis()
    value["status"] = "certain"
    with pytest.raises(ValidationError, match="status"):
        validate_diagnosis(value, _diagnosis_state())
    value = _supported_diagnosis()
    value["invented_metric"] = 42
    with pytest.raises(ValidationError, match="invented_metric"):
        validate_diagnosis(value, _diagnosis_state())
    state = _diagnosis_state()
    state["evidence"].append(deepcopy(state["evidence"][0]))
    with pytest.raises(ValueError, match="must be unique"):
        validate_diagnosis(_supported_diagnosis(), state)
