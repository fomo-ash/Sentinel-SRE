from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from sentinel.schemas import (
    InvestigationState,
    classifySchema,
    validate_action_proposal,
    validate_approval_decision,
    validate_diagnosis,
    validate_execution_receipt,
    validate_incident,
    validate_remediation_report,
    validate_verification_result,
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


def _remediation_contract():
    state = _diagnosis_state()
    state["final_severity"] = "unknown"
    state["diagnosis"] = _supported_diagnosis()
    proposal = {
        "proposal_id": "proposal-one",
        "incident_id": "diag-one",
        "service": "api",
        "action": "restore_simulated_db_login",
        "reason": "Current health and log observations show login rejection.",
        "current_evidence_ids": ["diag-one:health:ready"],
        "fingerprint": "a" * 64,
    }
    decision = {
        "decision_id": "decision-one",
        "proposal_id": "proposal-one",
        "proposal_fingerprint": "a" * 64,
        "incident_id": "diag-one",
        "service": "api",
        "decision": "approve",
        "operator_id": "operator-one",
        "decided_at": "2026-01-01T00:01:00Z",
    }
    receipt = {
        "action_id": "action-one",
        "incident_id": "diag-one",
        "service": "api",
        "action": "restore_simulated_db_login",
        "proposal_fingerprint": "a" * 64,
        "approval_reference": "decision-one",
        "status": "applied",
        "applied_at": "2026-01-01T00:02:00Z",
    }
    return state, proposal, decision, receipt


def test_phase6_contract_accepts_matching_current_records():
    state, proposal, decision, receipt = _remediation_contract()
    assert validate_action_proposal(proposal, state) == proposal
    assert validate_approval_decision(decision, proposal, state) == decision
    assert validate_execution_receipt(receipt, proposal, decision) == receipt
    verification = {
        "action_id": "action-one",
        "incident_id": "diag-one",
        "service": "api",
        "status": "verified",
        "observation_id": "restored-login",
        "observed_at": "2026-01-01T00:03:00Z",
        "failure_reason": None,
    }
    assert validate_verification_result(verification, receipt) == verification
    state["action_proposal"] = proposal
    state["approval_decision"] = decision
    state["execution_receipt"] = receipt
    state["verification_result"] = verification
    report = {
        "incident_id": "diag-one",
        "service": "api",
        "outcome": "verified",
        "final_severity": "unknown",
        "diagnosis_status": "supported",
        "summary": "The synthetic action was verified by a fresh observation.",
        "proposal_id": "proposal-one",
        "decision_id": "decision-one",
        "action_id": "action-one",
        "verification_status": "verified",
        "follow_up": [],
    }
    assert validate_remediation_report(report, state) == report


def test_phase6_proposal_rejects_non_current_evidence_and_unknown_fields():
    state, proposal, _, _ = _remediation_contract()
    proposal["current_evidence_ids"] = ["diag-other:log:failure"]
    with pytest.raises(ValueError, match="non-current evidence"):
        validate_action_proposal(proposal, state)
    proposal["current_evidence_ids"] = ["diag-one:health:ready"]
    proposal["command"] = "restart production"
    with pytest.raises(ValidationError, match="command"):
        validate_action_proposal(proposal, state)


def test_phase6_decision_requires_exact_proposal_and_operator():
    state, proposal, decision, _ = _remediation_contract()
    decision["proposal_fingerprint"] = "b" * 64
    with pytest.raises(ValueError, match="pending proposal"):
        validate_approval_decision(decision, proposal, state)
    decision["proposal_fingerprint"] = "a" * 64
    decision["operator_id"] = "  "
    with pytest.raises(ValueError, match="operator_id"):
        validate_approval_decision(decision, proposal, state)
    decision["operator_id"] = "operator-one"
    decision["decided_at"] = "2026-01-01T00:01:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_approval_decision(decision, proposal, state)


def test_phase6_receipt_requires_approval_and_matching_action():
    _, proposal, decision, receipt = _remediation_contract()
    decision["decision"] = "reject"
    with pytest.raises(ValueError, match="approved decision"):
        validate_execution_receipt(receipt, proposal, decision)
    decision["decision"] = "approve"
    receipt["approval_reference"] = "another-decision"
    with pytest.raises(ValueError, match="approved proposal"):
        validate_execution_receipt(receipt, proposal, decision)
    receipt["approval_reference"] = "decision-one"
    receipt["action"] = "arbitrary_command"
    with pytest.raises(ValidationError, match="action"):
        validate_execution_receipt(receipt, proposal, decision)


def test_phase6_verification_and_report_cannot_claim_unobserved_success():
    state, proposal, decision, receipt = _remediation_contract()
    verification = {
        "action_id": "action-one",
        "incident_id": "diag-one",
        "service": "api",
        "status": "verified",
        "observation_id": "restored-login",
        "observed_at": "2026-01-01T00:01:00Z",
        "failure_reason": None,
    }
    with pytest.raises(ValueError, match="predates action"):
        validate_verification_result(verification, receipt)
    state["action_proposal"] = proposal
    state["approval_decision"] = decision
    state["execution_receipt"] = receipt
    report = {
        "incident_id": "diag-one",
        "service": "api",
        "outcome": "verified",
        "final_severity": "unknown",
        "diagnosis_status": "supported",
        "summary": "Resolved.",
        "proposal_id": "proposal-one",
        "decision_id": "decision-one",
        "action_id": "action-one",
        "verification_status": "verified",
        "follow_up": [],
    }
    with pytest.raises(ValueError, match="unverified success"):
        validate_remediation_report(report, state)
