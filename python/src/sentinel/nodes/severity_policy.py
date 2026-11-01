"""Deterministic severity decision over the current incident's reported impact.

Model recommendations are advisory. Only verbatim references to the current
report can supply additional impact evidence when the baseline is uncertain.
"""

import re
from collections.abc import Mapping
from typing import Literal

Severity = Literal["unknown", "sev1", "sev2", "sev3"]

_SCOPE_PATTERNS: tuple[tuple[Severity, tuple[str, ...]], ...] = (
    (
        "sev1",
        (
            r"\ball (?:users|customers|requests)\b",
            r"\bevery (?:customer|payment attempt|request)\b",
            r"\bcomplete outage\b",
            r"\bentire service is down\b",
            r"\bnobody can sign in\b",
        ),
    ),
    (
        "sev2",
        (
            r"\bsome requests\b",
            r"\ba subset of (?:traffic|requests|users)\b",
            r"\bone in five (?:queries|requests)\b",
            r"\bintermittent failures\b",
            r"\bservice is degraded\b",
            r"\bone availability zone\b",
        ),
    ),
    (
        "sev3",
        (
            r"\bone user\b",
            r"\bsingle (?:request|product page)\b",
            r"\bonly one (?:account|test account)\b",
            r"\bone internal probe\b",
            r"\bisolated (?:issue|incident)\b",
        ),
    ),
)


def _negated_scope(summary: str, scope: Severity) -> bool:
    if scope == "sev1" and re.search(
        r"\bno\s+(?:customers|users|requests)\s+(?:are\s+)?"
        r"(?:affected|failing)\b",
        summary,
        re.I,
    ):
        return True
    for severity, patterns in _SCOPE_PATTERNS:
        if severity != scope:
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, summary, flags=re.IGNORECASE):
                prefix = summary[max(0, match.start() - 24) : match.start()]
                if re.search(r"\b(?:no|not|never|without)\s*$", prefix, re.I):
                    return True
    return False


def _supported_scope(summary: str, reference: str) -> Severity:
    """Map an exact report span to a severity, excluding simple negations."""
    scopes: set[Severity] = set()
    for severity, patterns in _SCOPE_PATTERNS:
        for pattern in patterns:
            for match in re.finditer(pattern, reference, flags=re.IGNORECASE):
                prefix = reference[max(0, match.start() - 16) : match.start()]
                if re.search(r"\b(?:no|not|never|without)\s*$", prefix, re.I):
                    continue
                scopes.add(severity)
                # A quote may omit the word that negates it in the full report.
                if _negated_scope(summary, severity):
                    return "unknown"
    if len(scopes) != 1:
        return "unknown"
    scope = scopes.pop()
    return scope


def severity_policy(
    incident: Mapping[str, object],
    baseline: Mapping[str, object],
    interpretation: Mapping[str, object] | None,
) -> dict[str, str]:
    """Return a severity and reason without trusting model severity or counts.

    A known deterministic baseline takes precedence. When it is unknown, a
    model may point to an exact span in the report; the policy maps that span
    using fixed scope rules. Missing, unsupported, or conflicting spans stay
    unknown. This decision never grants remediation approval.
    """
    baseline_severity = baseline.get("severity", "unknown")
    summary = incident.get("summary")
    if baseline_severity in ("sev1", "sev2", "sev3"):
        if isinstance(summary, str) and _negated_scope(summary, baseline_severity):
            return {
                "final_severity": "unknown",
                "severity_policy_reason": "conflicting_impact",
            }
        return {
            "final_severity": str(baseline_severity),
            "severity_policy_reason": "baseline_impact",
        }

    if not isinstance(summary, str) or not isinstance(interpretation, Mapping):
        return {
            "final_severity": "unknown",
            "severity_policy_reason": "unresolved_impact",
        }

    references = interpretation.get("source_references")
    if not isinstance(references, list) or not references:
        return {
            "final_severity": "unknown",
            "severity_policy_reason": "unresolved_impact",
        }

    scopes: set[Severity] = set()
    for reference in references:
        if not isinstance(reference, str) or not reference.strip():
            return {
                "final_severity": "unknown",
                "severity_policy_reason": "unsupported_reference",
            }
        # A plausible model claim is still unusable unless the current report says it.
        if reference.casefold() not in summary.casefold():
            return {
                "final_severity": "unknown",
                "severity_policy_reason": "unsupported_reference",
            }
        scope = _supported_scope(summary, reference)
        if scope == "unknown":
            return {
                "final_severity": "unknown",
                "severity_policy_reason": "unresolved_impact",
            }
        scopes.add(scope)

    if len(scopes) != 1:
        return {
            "final_severity": "unknown",
            "severity_policy_reason": "conflicting_impact",
        }
    return {"final_severity": scopes.pop(), "severity_policy_reason": "grounded_impact"}
