"""Raw incident facts and the state shared by graph nodes."""

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
    historical_matches: NotRequired[list[dict]]
    retrieval_failure_reason: NotRequired[RetrievalFailure | None]


def validate_incident(incident: IncidentReport) -> None:
    """Reject incomplete incident reports at the graph boundary."""
    for field in ("id", "service", "summary"):
        value = incident.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"incident.{field} must be a non-empty string")


_diagnosis_adapter = TypeAdapter(Diagnosis)


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
