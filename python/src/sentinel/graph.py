"""Validate a simulated incident and route optional model classification."""

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.nodes.classify import classify
from sentinel.nodes.llm_classify import ClassificationModel, build_llm_classify_node
from sentinel.nodes.severity_policy import severity_policy
from sentinel.schemas import InvestigationState, validate_incident


def receive_incident(state: InvestigationState) -> dict:
    """Receive and validate an incident."""
    validate_incident(state["incident"])
    return {"severity": "unknown", "symptoms": [], "hypotheses": [], "evidence": []}


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


def severity_policy_node(state: InvestigationState) -> dict:
    """Keep model interpretation separate from the final severity decision."""
    return severity_policy(
        state["incident"],
        {"severity": state.get("severity", "unknown")},
        state.get("model_interpretation"),
    )


def build_graph(model: ClassificationModel | None = None):
    """Create a fresh graph and in-memory checkpoint store per caller."""
    if model is None:

        def llm_classify_node(state: InvestigationState) -> dict:
            raise ValueError("LLM mode requires an injected classification model")

    else:
        llm_classify_node = build_llm_classify_node(model)

    workflow = StateGraph(InvestigationState)
    workflow.add_node("receive_incident", receive_incident)
    workflow.add_node("classify", classify_baseline)
    workflow.add_node("llm_classify", llm_classify_node)
    workflow.add_node("severity_policy", severity_policy_node)
    workflow.add_edge(START, "receive_incident")
    workflow.add_edge("receive_incident", "classify")
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
