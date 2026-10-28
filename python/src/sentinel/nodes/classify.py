"""Transparent keyword baseline for simulated incident descriptions."""

import re

from sentinel.schemas import InvestigationState, Severity

SYMPTOM_PATTERNS = {
    "database_connectivity": (
        "database connection",
        "connection pool",
        "postgresql connection",
        "database unavailable",
    ),
    "network_connectivity": (
        "network failure",
        "network outage",
        "dns failure",
        "connection refused",
    ),
    "elevated_errors": (
        "elevated error rate",
        "increased errors",
        "http 500",
        "requests failing",
    ),
    "high_latency": ("high latency", "slow requests", "request timeout", "timing out"),
    "service_outage": (
        "service is down",
        "entire service is down",
        "complete outage",
        "all requests are failing",
    ),
}

IMPACT_PATTERNS: tuple[tuple[Severity, tuple[str, ...]], ...] = (
    (
        "sev1",
        (
            "all requests are failing",
            "entire service is down",
            "complete outage",
            "all users are affected",
        ),
    ),
    (
        "sev2",
        (
            "elevated error rate",
            "increased errors",
            "intermittent failures",
            "many requests are failing",
            "service is degraded",
        ),
    ),
    ("sev3", ("one user", "single request", "isolated issue", "isolated incident")),
)

NEGATORS = {"no", "not", "without", "never", "isn't", "aren't"}


def _matches(text: str, phrase: str, *, negated: bool = False) -> bool:
    """Match a phrase with or without a nearby preceding negator."""
    for match in re.finditer(r"\b" + re.escape(phrase) + r"\b", text):
        clause = re.split(r"[.;,!]|\bbut\b|\bhowever\b", text[: match.start()])[-1]
        if bool(NEGATORS.intersection(clause.split()[-3:])) == negated:
            return True
    return False


def classify(state: InvestigationState) -> dict:
    """Return only the classification node's severity and symptom fields."""
    text = state["incident"]["summary"].lower()
    symptoms = [
        symptom
        for symptom, patterns in SYMPTOM_PATTERNS.items()
        if any(_matches(text, phrase) for phrase in patterns)
    ]
    severity: Severity = "unknown"
    for candidate, patterns in IMPACT_PATTERNS:
        if any(_matches(text, phrase) for phrase in patterns):
            severity = candidate
            break
    # Explicitly contradictory impact claims need review rather than a guess.
    if severity != "unknown" and any(
        _matches(text, phrase, negated=True)
        for candidate, patterns in IMPACT_PATTERNS
        if candidate == severity
        for phrase in patterns
    ):
        severity = "unknown"
    return {"severity": severity, "symptoms": symptoms}
