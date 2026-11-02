"""Validate an incident and route optional retrieval and model classification."""

import math
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.nodes.classify import classify
from sentinel.nodes.llm_classify import ClassificationModel, build_llm_classify_node
from sentinel.nodes.severity_policy import severity_policy
from sentinel.schemas import InvestigationState, validate_incident


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


def build_graph(
    model: ClassificationModel | None = None,
    *,
    retriever: HistoricalRetriever | None = None,
    retrieval_top_k: int = 3,
):
    """Create a fresh graph and in-memory checkpoint store per caller."""
    if isinstance(retrieval_top_k, bool) or not isinstance(retrieval_top_k, int):
        raise ValueError("retrieval_top_k must be a positive integer")
    if retrieval_top_k < 1:
        raise ValueError("retrieval_top_k must be a positive integer")

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

    workflow = StateGraph(InvestigationState)
    workflow.add_node("receive_incident", receive_incident)
    workflow.add_node("classify", classify_baseline)
    workflow.add_node("retrieve_history", retrieve_history)
    workflow.add_node("llm_classify", llm_classify_node)
    workflow.add_node("severity_policy", severity_policy_node)
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
    workflow.add_edge("severity_policy", END)
    return workflow.compile(checkpointer=InMemorySaver())
