import pytest

from sentinel.schemas import validate_incident


def test_incident_requires_nonempty_fields():
    with pytest.raises(ValueError, match="incident.summary"):
        validate_incident({"id": "inc-1", "service": "api", "summary": "  "})
