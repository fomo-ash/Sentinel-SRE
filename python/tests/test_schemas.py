import pytest
from pydantic import TypeAdapter, ValidationError

from sentinel.schemas import classifySchema, validate_incident


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
