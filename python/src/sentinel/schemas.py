"""Raw incident facts and the state shared by graph nodes."""

from typing import Annotated, Literal, NotRequired

from pydantic import Field
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


class IncidentReport(TypedDict):
    id: str
    service: str
    summary: str


class Evidence(TypedDict):
    source: str
    observation: str


class Hypothesis(TypedDict):
    cause: str
    rationale: str


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
    diagnosis: NotRequired[str]


def validate_incident(incident: IncidentReport) -> None:
    """Reject incomplete incident reports at the graph boundary."""
    for field in ("id", "service", "summary"):
        value = incident.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"incident.{field} must be a non-empty string")
