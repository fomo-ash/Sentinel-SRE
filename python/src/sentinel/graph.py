"""Classify incidents and optionally run bounded simulated diagnostics."""

import math
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.nodes.classify import classify
from sentinel.nodes.diagnose import diagnose_baseline
from sentinel.nodes.evaluate import evaluate_evidence
from sentinel.nodes.investigate import DIAGNOSTIC_PLAN, plan_diagnostics, run_diagnostic
from sentinel.nodes.llm_classify import ClassificationModel, build_llm_classify_node
from sentinel.nodes.severity_policy import severity_policy
from sentinel.schemas import InvestigationState, validate_incident
from sentinel.tools.diagnostics import DiagnosticClient, validate_diagnostic_request


class HistoricalRetriever(Protocol):
    """Small boundary for scored, provenance-bearing past incidents."""

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]: ...


def receive_incident(state: InvestigationState) -> dict:
    """Receive and validate an incident."""
    validate_incident(state["incident"])
    return {
        "severity": "unknown",
        "symptoms": [],
        "hypotheses": [],
        "evidence": [],
        "diagnostic_plan": [dict(check) for check in DIAGNOSTIC_PLAN],
        "completed_checks": [],
        "pending_check": None,
        "diagnostic_result": None,
        "diagnostic_error": None,
        "diagnostic_failures": [],
        "investigation_round": 0,
        "tool_call_count": 0,
        "diagnostic_latency_ms": 0.0,
        "last_attempts": 0,
        "last_latency_ms": 0.0,
        "investigation_stop_reason": None,
        "diagnosis": None,
        "diagnosis_source": None,
        "diagnosis_failure_reason": None,
        "historical_matches": [],
        "retrieval_failure_reason": None,
    }


def classify_baseline(state: InvestigationState) -> dict:
    """Record the source of the default deterministic classification."""
    return {
        **classify(state),
        "classification_source": "baseline",
        "failure_reason": None,
        "model_interpretation": None,
    }


def route_classification(
    state: InvestigationState,
    config: RunnableConfig,
) -> str:
    """Choose the caller's explicit mode; the baseline is the default."""
    mode = config.get("configurable", {}).get(
        "classification_mode",
        "deterministic",
    )

    if mode == "llm":
        return "llm"

    if mode == "deterministic":
        return "policy"

    raise ValueError(f"Unsupported classification mode: {mode}")


def route_history(state: InvestigationState, config: RunnableConfig) -> str:
    """Run retrieval only when explicitly requested by the caller."""
    mode = config.get("configurable", {}).get("retrieval_mode", "off")
    if mode == "off":
        return "classify"
    if mode == "history":
        return "retrieve"
    raise ValueError(f"Unsupported retrieval mode: {mode}")


def severity_policy_node(state: InvestigationState) -> dict:
    """Keep model interpretation separate from the final severity decision."""
    return severity_policy(
        state["incident"],
        {"severity": state.get("severity", "unknown")},
        state.get("model_interpretation"),
    )


def route_investigation(
    state: InvestigationState,
    config: RunnableConfig,
    *,
    client: DiagnosticClient | None,
) -> str:
    """Require an explicit opt-in and a synthetic incident before any read."""
    mode = config.get("configurable", {}).get("investigation_mode", "off")
    if mode == "off":
        return "off"
    if mode != "simulated":
        raise ValueError(f"Unsupported investigation mode: {mode}")
    if client is None:
        raise ValueError("Simulated investigation requires an injected client")
    incident = state["incident"]
    validate_diagnostic_request(incident["id"], incident["service"])
    return "simulated"


def route_plan(state: InvestigationState) -> str:
    return "read" if state.get("pending_check") is not None else "stop"


def route_evaluation(state: InvestigationState) -> str:
    return (
        "stop"
        if state.get("investigation_stop_reason") == "incident_not_found"
        else "continue"
    )


def finish_investigation(state: InvestigationState) -> dict:
    """End Phase 4 with observations and an explicit stop reason, never a cause."""
    return {"pending_check": None, "diagnostic_result": None}


def build_graph(
    model: ClassificationModel | None = None,
    *,
    retriever: HistoricalRetriever | None = None,
    retrieval_top_k: int = 3,
    diagnostic_client: DiagnosticClient | None = None,
    max_investigation_rounds: int = 3,
    max_tool_calls: int = 6,
    max_attempts_per_read: int = 2,
):
    """Create a fresh graph and in-memory checkpoint store per caller."""
    if isinstance(retrieval_top_k, bool) or not isinstance(retrieval_top_k, int):
        raise ValueError("retrieval_top_k must be a positive integer")
    if retrieval_top_k < 1:
        raise ValueError("retrieval_top_k must be a positive integer")
    for name, value, maximum in (
        ("max_investigation_rounds", max_investigation_rounds, 3),
        ("max_tool_calls", max_tool_calls, 6),
        ("max_attempts_per_read", max_attempts_per_read, 2),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")

    def retrieve_history(state: InvestigationState) -> dict:
        if retriever is None:
            raise ValueError("History mode requires an injected retriever")
        try:
            matches = retriever.retrieve(
                state["incident"]["summary"], top_k=retrieval_top_k
            )
            if not isinstance(matches, list) or len(matches) > retrieval_top_k:
                raise ValueError("Invalid retrieval result")
            for match in matches:
                if not isinstance(match, dict):
                    raise ValueError("Invalid retrieval match")
                incident = match.get("historical_incident")
                score = match.get("score")
                if (
                    not isinstance(match.get("id"), str)
                    or not isinstance(incident, dict)
                    or incident.get("id") != match["id"]
                    or any(
                        not isinstance(incident.get(field), str)
                        or not incident[field].strip()
                        for field in ("summary", "confirmed_cause", "resolution")
                    )
                    or not isinstance(incident.get("provenance"), dict)
                    or incident["provenance"].get("type") != "synthetic"
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                    or not -1 <= score <= 1
                ):
                    raise ValueError("Invalid retrieval match")
        except Exception:
            # Retrieval is optional context, so classification can still run.
            return {
                "historical_matches": [],
                "retrieval_failure_reason": "retriever_error",
            }
        return {"historical_matches": matches, "retrieval_failure_reason": None}

    if model is None:

        def llm_classify_node(state: InvestigationState) -> dict:
            raise ValueError("LLM mode requires an injected classification model")

    else:
        llm_classify_node = build_llm_classify_node(model)

    def plan_node(state: InvestigationState) -> dict:
        return plan_diagnostics(
            state,
            max_rounds=max_investigation_rounds,
            max_tool_calls=max_tool_calls,
        )

    def run_node(state: InvestigationState) -> dict:
        if diagnostic_client is None:
            raise ValueError("Simulated investigation requires an injected client")
        return run_diagnostic(
            state,
            client=diagnostic_client,
            max_tool_calls=max_tool_calls,
            max_attempts_per_read=max_attempts_per_read,
        )

    def investigation_route(state: InvestigationState, config: RunnableConfig) -> str:
        return route_investigation(state, config, client=diagnostic_client)

    workflow = StateGraph(InvestigationState)
    workflow.add_node("receive_incident", receive_incident)
    workflow.add_node("classify", classify_baseline)
    workflow.add_node("retrieve_history", retrieve_history)
    workflow.add_node("llm_classify", llm_classify_node)
    workflow.add_node("severity_policy", severity_policy_node)
    workflow.add_node("plan_diagnostics", plan_node)
    workflow.add_node("run_diagnostic", run_node)
    workflow.add_node("evaluate_evidence", evaluate_evidence)
    workflow.add_node("finish_investigation", finish_investigation)
    workflow.add_node("diagnose_baseline", diagnose_baseline)
    workflow.add_edge(START, "receive_incident")
    workflow.add_conditional_edges(
        "receive_incident",
        route_history,
        {"retrieve": "retrieve_history", "classify": "classify"},
    )
    workflow.add_edge("retrieve_history", "classify")
    workflow.add_conditional_edges(
        "classify",
        route_classification,
        {
            "llm": "llm_classify",
            "policy": "severity_policy",
        },
    )
    workflow.add_edge("llm_classify", "severity_policy")
    workflow.add_conditional_edges(
        "severity_policy",
        investigation_route,
        {"off": END, "simulated": "plan_diagnostics"},
    )
    workflow.add_conditional_edges(
        "plan_diagnostics",
        route_plan,
        {"read": "run_diagnostic", "stop": "finish_investigation"},
    )
    workflow.add_edge("run_diagnostic", "evaluate_evidence")
    workflow.add_conditional_edges(
        "evaluate_evidence",
        route_evaluation,
        {"continue": "plan_diagnostics", "stop": "finish_investigation"},
    )
    workflow.add_edge("finish_investigation", "diagnose_baseline")
    workflow.add_edge("diagnose_baseline", END)
    return workflow.compile(checkpointer=InMemorySaver())
