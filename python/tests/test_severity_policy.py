import pytest

from sentinel.nodes.severity_policy import severity_policy


@pytest.mark.parametrize(
    ("summary", "baseline", "interpretation", "severity", "reason"),
    [
        (
            "All requests are failing.",
            "sev1",
            {"recommended_severity": "sev3", "source_references": ["All requests"]},
            "sev1",
            "baseline_impact",
        ),
        (
            "All users are affected, but not all customers are affected.",
            "sev1",
            None,
            "unknown",
            "conflicting_impact",
        ),
        (
            "All users are affected, but no customers are affected.",
            "sev1",
            None,
            "unknown",
            "conflicting_impact",
        ),
        (
            "Every customer cannot reach the service.",
            "unknown",
            {
                "recommended_severity": "sev3",
                "source_references": ["Every customer"],
            },
            "sev1",
            "grounded_impact",
        ),
        (
            "Database alarm fired; impact unknown.",
            "unknown",
            {"recommended_severity": "sev1", "source_references": []},
            "unknown",
            "unresolved_impact",
        ),
        (
            "Database alarm fired; impact unknown.",
            "unknown",
            {
                "recommended_severity": "sev1",
                "source_references": ["All 500 users affected"],
            },
            "unknown",
            "unsupported_reference",
        ),
        (
            "All users are affected, but not all users are affected.",
            "unknown",
            {
                "recommended_severity": "sev1",
                "source_references": ["All users are affected"],
            },
            "unknown",
            "unresolved_impact",
        ),
        (
            "No complete outage has been confirmed.",
            "unknown",
            {
                "recommended_severity": "sev1",
                "source_references": ["complete outage"],
            },
            "unknown",
            "unresolved_impact",
        ),
        (
            "One user reports a problem; some requests fail.",
            "unknown",
            {
                "recommended_severity": "sev1",
                "source_references": ["One user", "some requests"],
            },
            "unknown",
            "conflicting_impact",
        ),
    ],
)
def test_policy_uses_only_supported_reported_scope(
    summary, baseline, interpretation, severity, reason
):
    result = severity_policy(
        {"id": "inc-1", "service": "api", "summary": summary},
        {"severity": baseline},
        interpretation,
    )
    assert result == {"final_severity": severity, "severity_policy_reason": reason}
    assert "approved" not in result
