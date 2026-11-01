import pytest

from sentinel.graph import build_graph


def test_intake_sets_initial_state_and_checkpoints():
    graph = build_graph()
    config = {"configurable": {"thread_id": "inc-1"}}
    result = graph.invoke(
        {"incident": {"id": "inc-1", "service": "api", "summary": "5xx spike"}},
        config,
    )
    assert result["severity"] == "unknown"
    assert result["final_severity"] == "unknown"
    assert result["classification_source"] == "baseline"
    assert result["failure_reason"] is None
    assert result["model_interpretation"] is None
    assert result["evidence"] == []
    assert graph.get_state(config).values == result


def test_optional_llm_route_applies_grounded_policy_and_checkpoints():
    class FakeModel:
        def invoke(self, description):
            return {
                "recommended_severity": "sev3",
                "symptoms": [],
                "rationale": "Every customer is affected.",
                "affected_components": ["edge"],
                "reported_impact": ["Every customer cannot reach the service."],
                "source_references": ["Every customer"],
                "uncertainties": [],
            }

    graph = build_graph(FakeModel())
    config = {"configurable": {"thread_id": "inc-llm", "classification_mode": "llm"}}
    result = graph.invoke(
        {
            "incident": {
                "id": "inc-llm",
                "service": "edge",
                "summary": "Every customer cannot reach the service.",
            }
        },
        config,
    )
    assert result["severity"] == "unknown"
    assert result["final_severity"] == "sev1"
    assert result["severity_policy_reason"] == "grounded_impact"
    assert result["classification_source"] == "llm"
    assert result["model_interpretation"]["recommended_severity"] == "sev3"
    assert graph.get_state(config).values == result


def test_optional_llm_route_falls_back_to_baseline_after_invalid_repair():
    class InvalidModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, description):
            self.calls += 1
            return {"recommended_severity": "critical"}

    model = InvalidModel()
    result = build_graph(model).invoke(
        {
            "incident": {
                "id": "inc-fallback",
                "service": "api",
                "summary": "One user reports slow requests",
            }
        },
        {"configurable": {"thread_id": "inc-fallback", "classification_mode": "llm"}},
    )
    assert model.calls == 2
    assert result["classification_source"] == "baseline_fallback"
    assert result["failure_reason"] == "invalid_output"
    assert result["final_severity"] == "sev3"


def test_optional_llm_route_falls_back_after_bounded_timeout():
    class TimeoutModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, description):
            self.calls += 1
            raise TimeoutError("temporary timeout")

    model = TimeoutModel()
    result = build_graph(model).invoke(
        {
            "incident": {
                "id": "inc-timeout",
                "service": "api",
                "summary": "Impact under investigation",
            }
        },
        {"configurable": {"thread_id": "inc-timeout", "classification_mode": "llm"}},
    )
    assert model.calls == 3
    assert result["classification_source"] == "baseline_fallback"
    assert result["failure_reason"] == "timeout"
    assert result["final_severity"] == "unknown"


@pytest.mark.parametrize("mode", ["llm", "unsupported"])
def test_graph_rejects_unavailable_or_invalid_model_route(mode):
    with pytest.raises(ValueError):
        build_graph().invoke(
            {"incident": {"id": mode, "service": "api", "summary": "Alarm"}},
            {"configurable": {"thread_id": mode, "classification_mode": mode}},
        )
