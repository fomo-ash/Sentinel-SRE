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
    assert result["action_proposal"] is None
    assert result["approval_decision"] is None
    assert result["approval_mode"] is None
    assert result["execution_receipt"] is None
    assert result["verification_result"] is None
    assert result["remediation_outcome"] is None
    assert result["remediation_report"] is None
    assert graph.get_state(config).values == result


def test_intake_clears_caller_supplied_phase6_records_on_reused_thread():
    graph = build_graph()
    config = {"configurable": {"thread_id": "phase6-reset"}}
    incident = {"id": "diag-one", "service": "api", "summary": "Alarm"}
    graph.invoke({"incident": incident}, config)
    graph.update_state(
        config,
        {
            "action_proposal": {"proposal_id": "old"},
            "approval_decision": {"decision": "approve"},
            "approval_mode": "simulated",
            "execution_receipt": {"action_id": "old"},
            "verification_result": {"status": "verified"},
            "remediation_outcome": "verified",
            "remediation_report": {"summary": "old"},
        },
    )
    result = graph.invoke(
        {
            "incident": incident,
            "action_proposal": {"proposal_id": "forged"},
            "approval_decision": {"decision": "approve"},
            "approval_mode": "simulated",
            "execution_receipt": {"action_id": "forged"},
        },
        config,
    )
    assert all(
        result[field] is None
        for field in (
            "action_proposal",
            "approval_decision",
            "approval_mode",
            "execution_receipt",
            "execution_failure_reason",
            "verification_result",
            "remediation_outcome",
            "remediation_report",
        )
    )
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


def test_opt_in_history_reaches_model_as_past_context_and_is_checkpointed():
    class FakeRetriever:
        def retrieve(self, query, top_k=3):
            assert query == "One user reports slow requests"
            assert top_k == 2
            return [
                {
                    "id": "hist-test",
                    "score": 0.8,
                    "historical_incident": {
                        "id": "hist-test",
                        "summary": "All users lost access in a past incident.",
                        "confirmed_cause": "A prior queue stalled.",
                        "resolution": "The queue was replayed.",
                        "provenance": {"type": "synthetic"},
                    },
                }
            ]

    class FakeModel:
        def __init__(self):
            self.prompts = []

        def invoke(self, description):
            self.prompts.append(description)
            return {
                "recommended_severity": "sev1",
                "symptoms": ["high_latency"],
                "rationale": "One user reports slow requests.",
                "affected_components": ["api"],
                "reported_impact": ["One user reports slow requests"],
                "source_references": ["One user reports slow requests"],
                "uncertainties": ["Check whether the prior queue issue recurred."],
            }

    model = FakeModel()
    graph = build_graph(model, retriever=FakeRetriever(), retrieval_top_k=2)
    config = {
        "configurable": {
            "thread_id": "inc-history",
            "classification_mode": "llm",
            "retrieval_mode": "history",
        }
    }
    result = graph.invoke(
        {
            "incident": {
                "id": "inc-history",
                "service": "api",
                "summary": "One user reports slow requests",
            }
        },
        config,
    )

    assert result["historical_matches"][0]["id"] == "hist-test"
    assert result["retrieval_failure_reason"] is None
    assert "HISTORICAL CONTEXT" in model.prompts[0]
    assert "All users lost access in a past incident" in model.prompts[0]
    assert result["final_severity"] == "sev3"
    assert result["evidence"] == []
    assert graph.get_state(config).values == result


def test_retrieval_failure_keeps_classification_available():
    class FailedRetriever:
        def retrieve(self, query, top_k=3):
            raise TimeoutError("local embedding timeout")

    result = build_graph(retriever=FailedRetriever()).invoke(
        {
            "incident": {
                "id": "inc-retrieval-failure",
                "service": "api",
                "summary": "One user reports slow requests",
            }
        },
        {
            "configurable": {
                "thread_id": "inc-retrieval-failure",
                "retrieval_mode": "history",
            }
        },
    )
    assert result["historical_matches"] == []
    assert result["retrieval_failure_reason"] == "retriever_error"
    assert result["final_severity"] == "sev3"


def test_malformed_retrieval_result_is_discarded_before_model_prompt():
    class MalformedRetriever:
        def retrieve(self, query, top_k=3):
            return [
                {
                    "id": "hist-bad",
                    "score": 0.9,
                    "historical_incident": {
                        "id": "hist-bad",
                        "provenance": {"type": "synthetic"},
                    },
                }
            ]

    result = build_graph(retriever=MalformedRetriever()).invoke(
        {"incident": {"id": "inc-bad-match", "service": "api", "summary": "Alarm"}},
        {
            "configurable": {
                "thread_id": "inc-bad-match",
                "retrieval_mode": "history",
            }
        },
    )
    assert result["historical_matches"] == []
    assert result["retrieval_failure_reason"] == "retriever_error"
    assert result["final_severity"] == "unknown"


@pytest.mark.parametrize("mode", ["history", "unsupported"])
def test_graph_rejects_missing_retriever_or_unknown_history_mode(mode):
    with pytest.raises(ValueError):
        build_graph().invoke(
            {"incident": {"id": mode, "service": "api", "summary": "Alarm"}},
            {"configurable": {"thread_id": mode, "retrieval_mode": mode}},
        )
