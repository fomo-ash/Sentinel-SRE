import pytest
from pydantic import ValidationError

from sentinel.nodes.llm_classify import interpret_incident


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.received = []

    def invoke(self, description: str) -> dict:
        self.received.append(description)
        return self.response


def valid_response():
    return {
        "recommended_severity": "sev2",
        "symptoms": ["high_latency"],
        "rationale": "The report describes partial timeouts.",
        "affected_components": ["api"],
        "reported_impact": ["Some requests time out."],
        "source_references": [],
        "uncertainties": ["Affected-user count is unknown."],
    }


def test_interpret_incident_validates_fake_model_response():
    model = FakeModel(valid_response())
    result = interpret_incident("Some API requests time out", model)
    assert result["recommended_severity"] == "sev2"
    assert result["uncertainties"] == ["Affected-user count is unknown."]
    assert model.received == ["Some API requests time out"]


def test_interpret_incident_rejects_invalid_model_output():
    model = FakeModel({**valid_response(), "recommended_severity": "critical"})
    with pytest.raises(ValidationError, match="recommended_severity"):
        interpret_incident("Some API requests time out", model)
    assert len(model.received) == 2
    assert "Some API requests time out" in model.received[1]
    assert "recommended_severity" in model.received[1]
    assert "required field" in model.received[1]


def test_interpret_incident_repairs_invalid_output_once():
    class RepairModel:
        def __init__(self):
            self.received = []

        def invoke(self, description: str) -> dict:
            self.received.append(description)
            if len(self.received) == 1:
                return {**valid_response(), "recommended_severity": "critical"}
            return valid_response()

    model = RepairModel()
    result = interpret_incident("Some API requests time out", model)
    assert result["recommended_severity"] == "sev2"
    assert len(model.received) == 2
    assert model.received[0] == "Some API requests time out"
    assert (
        "Original incident description: Some API requests time out" in model.received[1]
    )
    assert "Validation errors:" in model.received[1]


def test_interpret_incident_propagates_model_timeout():
    class TimeoutModel:
        def invoke(self, description: str) -> dict:
            raise TimeoutError("model timed out")

    with pytest.raises(TimeoutError, match="model timed out"):
        interpret_incident("Some API requests time out", TimeoutModel())
