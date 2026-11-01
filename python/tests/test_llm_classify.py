import pytest
from pydantic import ValidationError

from sentinel.nodes.llm_classify import (
    TransientProviderError,
    build_llm_classify_node,
    interpret_incident,
)


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


def _state(summary="Impact under investigation"):
    return {"incident": {"id": "inc-1", "service": "api", "summary": summary}}


def test_llm_node_preserves_interpretation_separately_from_final_severity():
    result = build_llm_classify_node(FakeModel(valid_response()))(_state())
    assert result == {
        "model_interpretation": valid_response(),
        "symptoms": ["high_latency"],
        "classification_source": "llm",
        "failure_reason": None,
    }
    assert "severity" not in result
    assert "final_severity" not in result


def test_invalid_repair_falls_back_after_one_repair_attempt():
    model = FakeModel({**valid_response(), "recommended_severity": "critical"})
    result = build_llm_classify_node(model)(_state())
    assert len(model.received) == 2
    assert result["severity"] == "unknown"
    assert result["classification_source"] == "baseline_fallback"
    assert result["failure_reason"] == "invalid_output"
    assert result["model_interpretation"] is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError("timed out"), "timeout"),
        (ConnectionError("unavailable"), "provider_unavailable"),
        (TransientProviderError("rate_limited"), "rate_limited"),
    ],
)
def test_transient_failure_has_bounded_retries_and_baseline_fallback(error, reason):
    class FailingModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, description):
            self.calls += 1
            raise error

    model = FailingModel()
    delays = []
    result = build_llm_classify_node(model, sleep=delays.append)(
        _state("One user reports slow requests")
    )
    assert model.calls == 3
    assert delays == [0.1, 0.2]
    assert result["severity"] == "sev3"
    assert result["classification_source"] == "baseline_fallback"
    assert result["failure_reason"] == reason


def test_transient_failure_then_success_uses_model_result():
    class RecoveringModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, description):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary timeout")
            return valid_response()

    model = RecoveringModel()
    delays = []
    result = build_llm_classify_node(model, sleep=delays.append)(_state())
    assert model.calls == 2
    assert delays == [0.1]
    assert result["classification_source"] == "llm"
    assert result["failure_reason"] is None


def test_permanent_provider_error_falls_back_without_retry():
    class FailingModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, description):
            self.calls += 1
            raise RuntimeError("bad credentials")

    model = FailingModel()
    result = build_llm_classify_node(model)(_state())
    assert model.calls == 1
    assert result["severity"] == "unknown"
    assert result["failure_reason"] == "provider_error"
