"""Raw incident facts and the state shared by graph nodes."""

from datetime import datetime
from typing import Annotated, Literal, NotRequired

from pydantic import ConfigDict, Field, TypeAdapter, with_config
from typing_extensions import TypedDict

Severity = Literal["unknown", "sev1", "sev2", "sev3"]
Symptom = Literal[
    "database_connectivity",
    "network_connectivity",
    "elevated_errors",
    "high_latency",
    "service_outage",
]
ClassificationSource = Literal["baseline", "llm", "baseline_fallback"]
ClassificationFailure = Literal[
    "invalid_output",
    "timeout",
    "provider_unavailable",
    "rate_limited",
    "provider_error",
]
RetrievalFailure = Literal["retriever_error"]
DiagnosticKind = Literal["health", "metrics", "logs"]
DiagnosticFailureCategory = Literal[
    "timeout", "unavailable", "not_found", "invalid_response"
]
DiagnosisStatus = Literal["supported", "inconclusive"]
DiagnosisSource = Literal["deterministic", "llm", "fallback"]
DiagnosisFailure = Literal[
    "invalid_output",
    "timeout",
    "provider_unavailable",
    "rate_limited",
    "provider_error",
]
InvestigationStopReason = Literal[
    "checks_complete",
    "incident_not_found",
    "max_rounds",
    "max_tool_calls",
    "no_usable_data",
]
RemediationAction = Literal["restore_simulated_db_login"]
NoActionReason = Literal[
    "ineligible_incident",
    "incomplete_diagnostics",
    "unsupported_diagnosis",
    "unsupported_evidence",
    "invalid_approval_response",
]
RemediationOutcome = Literal[
    "no_action",
    "awaiting_approval",
    "rejected",
    "approved",
    "execution_failed",
    "execution_unknown",
    "verification_failed",
    "verification_unknown",
    "verified",
]
ExecutionFailure = Literal[
    "timeout", "unavailable", "invalid_response", "conflict", "rejected"
]
VerificationFailure = Literal[
    "timeout", "unavailable", "not_found", "invalid_response", "stale", "unhealthy"
]

BoundedID = Annotated[str, Field(min_length=1, max_length=128)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=512)]
Timestamp = Annotated[str, Field(min_length=1, max_length=40)]


class IncidentReport(TypedDict):
    id: str
    service: str
    summary: str


class Evidence(TypedDict):
    id: str
    incident_id: str
    service: str
    kind: Literal["health", "metric", "log"]
    source_ref: str
    observed_at: str
    observation: str


@with_config(ConfigDict(extra="forbid"))
class DiagnosisHypothesis(TypedDict):
    cause: str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: NotRequired[list[str]]


class DiagnosticCheck(TypedDict):
    kind: DiagnosticKind
    reason: str


class DiagnosticFailure(TypedDict):
    kind: DiagnosticKind
    category: DiagnosticFailureCategory
    attempts: int
    latency_ms: float


class Hypothesis(TypedDict):
    cause: str
    rationale: str


@with_config(ConfigDict(extra="forbid"))
class Diagnosis(TypedDict):
    status: DiagnosisStatus
    summary: str
    hypotheses: list[DiagnosisHypothesis]
    unresolved_questions: list[str]
    historical_context_ids: list[str]
    source: DiagnosisSource


@with_config(ConfigDict(extra="forbid", strict=True))
class ActionProposal(TypedDict):
    proposal_id: BoundedID
    incident_id: BoundedID
    service: BoundedID
    action: RemediationAction
    reason: BoundedText
    current_evidence_ids: Annotated[list[BoundedID], Field(min_length=1, max_length=20)]
    fingerprint: Fingerprint


@with_config(ConfigDict(extra="forbid", strict=True))
class ApprovalDecision(TypedDict):
    decision_id: BoundedID
    proposal_id: BoundedID
    proposal_fingerprint: Fingerprint
    incident_id: BoundedID
    service: BoundedID
    decision: Literal["approve", "reject"]
    operator_id: BoundedID
    decided_at: Timestamp


@with_config(ConfigDict(extra="forbid", strict=True))
class ApprovalResponse(TypedDict):
    """Human-supplied fields; workflow-owned ID and time are added on resume."""

    thread_id: BoundedID
    proposal_id: BoundedID
    proposal_fingerprint: Fingerprint
    incident_id: BoundedID
    service: BoundedID
    decision: Literal["approve", "reject"]
    operator_id: BoundedID


@with_config(ConfigDict(extra="forbid", strict=True))
class ExecutionReceipt(TypedDict):
    action_id: BoundedID
    incident_id: BoundedID
    service: BoundedID
    action: RemediationAction
    proposal_fingerprint: Fingerprint
    approval_reference: BoundedID
    status: Literal["applied"]
    applied_at: Timestamp


@with_config(ConfigDict(extra="forbid", strict=True))
class VerificationResult(TypedDict):
    action_id: BoundedID
    incident_id: BoundedID
    service: BoundedID
    status: Literal["verified", "failed", "unknown"]
    observation_id: BoundedID | None
    observed_at: Timestamp | None
    failure_reason: VerificationFailure | None


@with_config(ConfigDict(extra="forbid", strict=True))
class RemediationReport(TypedDict):
    incident_id: BoundedID
    service: BoundedID
    outcome: RemediationOutcome
    final_severity: Severity
    diagnosis_status: DiagnosisStatus | None
    summary: BoundedText
    proposal_id: BoundedID | None
    decision_id: BoundedID | None
    action_id: BoundedID | None
    verification_status: Literal["verified", "failed", "unknown"] | None
    follow_up: Annotated[list[BoundedText], Field(max_length=10)]


@with_config(ConfigDict(extra="forbid"))
class ModelDiagnosis(TypedDict):
    """Model-owned fields; the workflow sets provenance and severity."""

    status: DiagnosisStatus
    summary: str
    hypotheses: list[DiagnosisHypothesis]
    unresolved_questions: list[str]
    historical_context_ids: list[str]


class classifySchema(TypedDict):
    """Structured interpretation returned by a future classifier model."""

    recommended_severity: Severity
    symptoms: list[Symptom]
    confidence: NotRequired[Annotated[float, Field(ge=0, le=1)]]
    rationale: str
    affected_components: list[str]
    reported_impact: list[str]
    source_references: list[str]
    uncertainties: list[str]


class InvestigationState(TypedDict):
    incident: IncidentReport
    severity: NotRequired[Severity]
    # A model recommendation cannot become the policy decision by assignment.
    final_severity: NotRequired[Severity]
    severity_policy_reason: NotRequired[str]
    symptoms: NotRequired[list[str]]
    model_interpretation: NotRequired[classifySchema | None]
    classification_source: NotRequired[ClassificationSource]
    failure_reason: NotRequired[ClassificationFailure | None]
    hypotheses: NotRequired[list[Hypothesis]]
    evidence: NotRequired[list[Evidence]]
    diagnostic_plan: NotRequired[list[DiagnosticCheck]]
    completed_checks: NotRequired[list[DiagnosticKind]]
    pending_check: NotRequired[DiagnosticCheck | None]
    diagnostic_result: NotRequired[dict | None]
    diagnostic_error: NotRequired[DiagnosticFailureCategory | None]
    diagnostic_failures: NotRequired[list[DiagnosticFailure]]
    investigation_round: NotRequired[int]
    tool_call_count: NotRequired[int]
    diagnostic_latency_ms: NotRequired[float]
    last_attempts: NotRequired[int]
    last_latency_ms: NotRequired[float]
    investigation_stop_reason: NotRequired[InvestigationStopReason | None]
    diagnosis: NotRequired[Diagnosis | None]
    diagnosis_source: NotRequired[DiagnosisSource | None]
    diagnosis_failure_reason: NotRequired[DiagnosisFailure | None]
    diagnosis_model_calls: NotRequired[int]
    diagnosis_repair_attempted: NotRequired[bool]
    diagnosis_latency_ms: NotRequired[float]
    historical_matches: NotRequired[list[dict]]
    retrieval_failure_reason: NotRequired[RetrievalFailure | None]
    action_proposal: NotRequired[ActionProposal | None]
    no_action_reason: NotRequired[NoActionReason | None]
    approval_decision: NotRequired[ApprovalDecision | None]
    approval_mode: NotRequired[Literal["approval", "simulated"] | None]
    execution_receipt: NotRequired[ExecutionReceipt | None]
    execution_failure_reason: NotRequired[ExecutionFailure | None]
    verification_result: NotRequired[VerificationResult | None]
    remediation_outcome: NotRequired[RemediationOutcome | None]
    remediation_report: NotRequired[RemediationReport | None]


def validate_incident(incident: IncidentReport) -> None:
    """Reject incomplete incident reports at the graph boundary."""
    for field in ("id", "service", "summary"):
        value = incident.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"incident.{field} must be a non-empty string")


_diagnosis_adapter = TypeAdapter(Diagnosis)
_proposal_adapter = TypeAdapter(ActionProposal)
_approval_adapter = TypeAdapter(ApprovalDecision)
_approval_response_adapter = TypeAdapter(ApprovalResponse)
_receipt_adapter = TypeAdapter(ExecutionReceipt)
_verification_adapter = TypeAdapter(VerificationResult)
_report_adapter = TypeAdapter(RemediationReport)


def _require_nonblank(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must be nonblank")


def _require_timestamp(value: str, field: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware timestamp")


def validate_action_proposal(
    value: object, state: InvestigationState
) -> ActionProposal:
    """Validate a bounded proposal against current incident evidence."""
    proposal = _proposal_adapter.validate_python(value, strict=True)
    incident = state["incident"]
    if (proposal["incident_id"], proposal["service"]) != (
        incident["id"],
        incident["service"],
    ):
        raise ValueError("Proposal incident or service does not match")
    for field in ("proposal_id", "incident_id", "service", "reason"):
        _require_nonblank(proposal[field], field)
    ids = proposal["current_evidence_ids"]
    if len(ids) != len(set(ids)) or any(not item.strip() for item in ids):
        raise ValueError("Proposal evidence IDs must be unique and nonblank")
    current_ids = {
        item["id"]
        for item in state.get("evidence", [])
        if item["incident_id"] == incident["id"]
        and item["service"] == incident["service"]
    }
    if not set(ids) <= current_ids:
        raise ValueError("Proposal cites non-current evidence")
    return proposal


def validate_approval_decision(
    value: object, proposal: ActionProposal, state: InvestigationState
) -> ApprovalDecision:
    """Bind a recorded decision to the exact pending proposal."""
    decision = _approval_adapter.validate_python(value, strict=True)
    incident = state["incident"]
    if (
        decision["proposal_id"] != proposal["proposal_id"]
        or decision["proposal_fingerprint"] != proposal["fingerprint"]
        or decision["incident_id"] != proposal["incident_id"]
        or decision["service"] != proposal["service"]
        or decision["incident_id"] != incident["id"]
        or decision["service"] != incident["service"]
    ):
        raise ValueError("Decision does not match pending proposal")
    for field in ("decision_id", "operator_id"):
        _require_nonblank(decision[field], field)
    _require_timestamp(decision["decided_at"], "decided_at")
    return decision


def validate_approval_response(
    value: object, proposal: ActionProposal, thread_id: str
) -> ApprovalResponse:
    """Require an explicit decision for this thread and pending proposal."""
    response = _approval_response_adapter.validate_python(value, strict=True)
    if (
        response["thread_id"] != thread_id
        or response["proposal_id"] != proposal["proposal_id"]
        or response["proposal_fingerprint"] != proposal["fingerprint"]
        or response["incident_id"] != proposal["incident_id"]
        or response["service"] != proposal["service"]
    ):
        raise ValueError("Approval response does not match pending thread and proposal")
    _require_nonblank(response["operator_id"], "operator_id")
    return response


def validate_execution_receipt(
    value: object, proposal: ActionProposal, approval: ApprovalDecision
) -> ExecutionReceipt:
    """Accept only a receipt for the exact approved synthetic action."""
    receipt = _receipt_adapter.validate_python(value, strict=True)
    if approval["decision"] != "approve":
        raise ValueError("Execution requires an approved decision")
    if (
        receipt["incident_id"] != proposal["incident_id"]
        or receipt["service"] != proposal["service"]
        or receipt["action"] != proposal["action"]
        or receipt["proposal_fingerprint"] != proposal["fingerprint"]
        or receipt["approval_reference"] != approval["decision_id"]
    ):
        raise ValueError("Receipt does not match approved proposal")
    _require_nonblank(receipt["action_id"], "action_id")
    _require_timestamp(receipt["applied_at"], "applied_at")
    return receipt


def validate_verification_result(
    value: object, receipt: ExecutionReceipt
) -> VerificationResult:
    """Validate a post-action result without treating missing data as success."""
    result = _verification_adapter.validate_python(value, strict=True)
    if (result["action_id"], result["incident_id"], result["service"]) != (
        receipt["action_id"],
        receipt["incident_id"],
        receipt["service"],
    ):
        raise ValueError("Verification does not match receipt")
    if result["status"] == "verified":
        if (
            result["observation_id"] is None
            or result["observed_at"] is None
            or result["failure_reason"] is not None
        ):
            raise ValueError("Verified result requires an observation without failure")
        _require_nonblank(result["observation_id"], "observation_id")
        _require_timestamp(result["observed_at"], "observed_at")
        applied = datetime.fromisoformat(receipt["applied_at"].replace("Z", "+00:00"))
        observed = datetime.fromisoformat(result["observed_at"].replace("Z", "+00:00"))
        if observed < applied:
            raise ValueError("Verification observation predates action")
    else:
        if result["failure_reason"] is None:
            raise ValueError("Unverified result requires a failure reason")
        if result["observed_at"] is not None:
            _require_timestamp(result["observed_at"], "observed_at")
        if result["observation_id"] is not None:
            _require_nonblank(result["observation_id"], "observation_id")
    return result


def validate_remediation_report(
    value: object, state: InvestigationState
) -> RemediationReport:
    """Reject a report that changes incident facts or claims unsupported success."""
    report = _report_adapter.validate_python(value, strict=True)
    incident = state["incident"]
    if (report["incident_id"], report["service"]) != (
        incident["id"],
        incident["service"],
    ):
        raise ValueError("Report incident or service does not match")
    if report["final_severity"] != state.get("final_severity"):
        raise ValueError("Report changes final severity")
    diagnosis = state.get("diagnosis")
    if report["diagnosis_status"] != (diagnosis["status"] if diagnosis else None):
        raise ValueError("Report changes diagnosis status")
    proposal = state.get("action_proposal")
    approval = state.get("approval_decision")
    receipt = state.get("execution_receipt")
    for field, expected in (
        ("proposal_id", proposal["proposal_id"] if proposal else None),
        ("decision_id", approval["decision_id"] if approval else None),
        ("action_id", receipt["action_id"] if receipt else None),
    ):
        if report[field] != expected:
            raise ValueError(f"Report {field} does not match workflow state")
    _require_nonblank(report["summary"], "summary")
    for item in report["follow_up"]:
        _require_nonblank(item, "follow_up item")
    verification = state.get("verification_result")
    if report["outcome"] == "verified" and (
        approval is None
        or approval["decision"] != "approve"
        or receipt is None
        or verification is None
        or verification["status"] != "verified"
        or report["verification_status"] != "verified"
    ):
        raise ValueError("Report claims unverified success")
    return report


def validate_diagnosis(value: object, state: InvestigationState) -> Diagnosis:
    """Accept only well-shaped diagnoses citing current-incident evidence."""
    diagnosis = _diagnosis_adapter.validate_python(value, strict=True)
    incident = state["incident"]
    evidence = state.get("evidence", [])
    all_evidence_ids = [item["id"] for item in evidence]
    if len(all_evidence_ids) != len(set(all_evidence_ids)):
        raise ValueError("Current evidence IDs must be unique")
    current_ids = {
        item["id"]
        for item in evidence
        if item["incident_id"] == incident["id"]
        and item["service"] == incident["service"]
    }
    history_ids = {
        item["id"]
        for item in state.get("historical_matches", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    if not diagnosis["summary"].strip():
        raise ValueError("Diagnosis summary must be nonempty")
    if not set(diagnosis["historical_context_ids"]) <= history_ids:
        raise ValueError("Unknown historical context ID")
    if diagnosis["status"] == "supported" and not diagnosis["hypotheses"]:
        raise ValueError("Supported diagnosis needs a hypothesis")

    for hypothesis in diagnosis["hypotheses"]:
        if not hypothesis["cause"].strip():
            raise ValueError("Hypothesis cause must be nonempty")
        supporting = hypothesis["supporting_evidence_ids"]
        if diagnosis["status"] == "supported" and not supporting:
            raise ValueError("Supported hypothesis needs current evidence")
        for field in ("supporting_evidence_ids", "contradicting_evidence_ids"):
            references = set(hypothesis.get(field, []))
            if references & history_ids or not references <= current_ids:
                raise ValueError(f"{field} contains non-current evidence")
    return diagnosis
