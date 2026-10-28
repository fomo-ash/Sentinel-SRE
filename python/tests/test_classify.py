import pytest

from sentinel.graph import build_graph
from sentinel.nodes.classify import classify


@pytest.mark.parametrize(
    ("summary", "severity", "symptoms"),
    [
        (
            "Complete outage: database connection failed; all users are affected",
            "sev1",
            ["database_connectivity", "service_outage"],
        ),
        (
            "Elevated error rate and many requests are failing",
            "sev2",
            ["elevated_errors"],
        ),
        ("One user reports slow requests", "sev3", ["high_latency"]),
        (
            "Database connection errors; impact under investigation",
            "unknown",
            ["database_connectivity"],
        ),
        (
            "DNS failure, network outage; impact unknown",
            "unknown",
            ["network_connectivity"],
        ),
        ("No complete outage, but elevated error rate", "sev2", ["elevated_errors"]),
        ("Not all users are affected; impact unclear", "unknown", []),
        (
            "One user reported a problem, but all requests are failing",
            "sev1",
            ["service_outage"],
        ),
        (
            "Complete outage, but no complete outage is confirmed",
            "unknown",
            ["service_outage"],
        ),
    ],
)
def test_classification_baseline(summary, severity, symptoms):
    incident = {"id": "inc-1", "service": "api", "summary": summary}
    assert classify({"incident": incident}) == {
        "severity": severity,
        "symptoms": symptoms,
    }


def test_graph_preserves_incident_and_classification():
    incident = {"id": "inc-2", "service": "api", "summary": "Complete outage"}
    result = build_graph().invoke(
        {"incident": incident}, {"configurable": {"thread_id": "inc-2"}}
    )
    assert result["incident"] == incident
    assert result["severity"] == "sev1"
