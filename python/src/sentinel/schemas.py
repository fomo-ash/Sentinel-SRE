"""Raw incident facts and the state shared by graph nodes."""

from typing import Literal, NotRequired, TypedDict

Severity = Literal["unknown", "sev1", "sev2", "sev3"]


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


class InvestigationState(TypedDict):
    incident: IncidentReport
    severity: NotRequired[Severity]
    symptoms: NotRequired[list[str]]
    hypotheses: NotRequired[list[Hypothesis]]
    evidence: NotRequired[list[Evidence]]
    diagnosis: NotRequired[str]


def validate_incident(incident: IncidentReport) -> None:
    """Reject incomplete incident reports at the graph boundary."""
    for field in ("id", "service", "summary"):
        value = incident.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"incident.{field} must be a non-empty string")
