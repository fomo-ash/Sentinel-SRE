"""Foundation graph: validate and classify a simulated incident."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.nodes.classify import classify
from sentinel.schemas import InvestigationState, validate_incident


def receive_incident(state: InvestigationState) -> dict:
    """Receive and validate an incident."""
    validate_incident(state["incident"])
    return {"severity": "unknown", "symptoms": [], "hypotheses": [], "evidence": []}


def build_graph():
    """Create a fresh graph and in-memory checkpoint store per caller."""
    workflow = StateGraph(InvestigationState)
    workflow.add_node("receive_incident", receive_incident)
    workflow.add_node("classify", classify)
    workflow.add_edge(START, "receive_incident")
    workflow.add_edge("receive_incident", "classify")
    workflow.add_edge("classify", END)
    return workflow.compile(checkpointer=InMemorySaver())
