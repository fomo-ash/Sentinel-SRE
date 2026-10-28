"""Milestone 1 graph: accept and validate an incident, then stop."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.schemas import InvestigationState, validate_incident


def intake(state: InvestigationState) -> dict:
    validate_incident(state["incident"])
    return {"severity": "unknown", "symptoms": [], "hypotheses": [], "evidence": []}


def build_graph():
    """Create a fresh graph and in-memory checkpoint store per caller."""
    workflow = StateGraph(InvestigationState)
    workflow.add_node("intake", intake)
    workflow.add_edge(START, "intake")
    workflow.add_edge("intake", END)
    return workflow.compile(checkpointer=InMemorySaver())
