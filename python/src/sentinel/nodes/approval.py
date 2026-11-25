"""Checkpointed human decision for one proposed synthetic action."""

import hashlib
import json
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import ValidationError

from sentinel.schemas import (
    InvestigationState,
    validate_action_proposal,
    validate_approval_decision,
    validate_approval_response,
)

_WARNING = (
    "Approval authorizes the single synthetic action shown in the proposal. "
    "In simulated remediation mode, the local action runs after this decision "
    "is recorded."
)


def mark_awaiting_approval(state: InvestigationState, config: RunnableConfig) -> dict:
    """Checkpoint a pending status before the interrupting node runs."""
    proposal = state.get("action_proposal")
    if proposal is None or state.get("approval_decision") is not None:
        raise ValueError("Approval requires one pending proposal")
    validate_action_proposal(proposal, state)
    mode = config.get("configurable", {}).get("remediation_mode")
    if mode not in ("approval", "simulated"):
        raise ValueError("Approval requires an explicit remediation mode")
    return {"remediation_outcome": "awaiting_approval", "approval_mode": mode}


def approval(state: InvestigationState, config: RunnableConfig) -> dict:
    """Pause for an explicit response and checkpoint it before any action call."""
    configurable = config.get("configurable", {})
    if state.get("approval_mode") not in ("approval", "simulated") or (
        configurable.get("remediation_mode") != state["approval_mode"]
    ):
        raise ValueError("Approval resume requires approval mode from the checkpoint")
    proposal = state.get("action_proposal")
    if proposal is None or state.get("remediation_outcome") != "awaiting_approval":
        raise ValueError("Approval requires a checkpointed pending proposal")
    proposal = validate_action_proposal(proposal, state)
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip() or len(thread_id) > 128:
        raise ValueError("Approval requires a nonempty thread_id")

    prompt = {
        "type": "remediation_approval",
        "thread_id": thread_id,
        "proposal": proposal,
        "current_evidence_ids": proposal["current_evidence_ids"],
        "warning": _WARNING,
    }
    for attempt in range(3):
        raw_response = interrupt(prompt)
        try:
            response = validate_approval_response(raw_response, proposal, thread_id)
        except (ValueError, ValidationError):
            if attempt == 2:
                return {
                    "approval_decision": None,
                    "no_action_reason": "invalid_approval_response",
                    "remediation_outcome": "no_action",
                }
            prompt = {**prompt, "validation_error": "invalid_approval_response"}
        else:
            break
    decided_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    decision_payload = {
        "thread_id": thread_id,
        "proposal_id": proposal["proposal_id"],
        "proposal_fingerprint": proposal["fingerprint"],
        "decision": response["decision"],
        "operator_id": response["operator_id"],
        "decided_at": decided_at,
    }
    decision_hash = hashlib.sha256(
        json.dumps(decision_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    decision = validate_approval_decision(
        {
            "decision_id": f"decision-{decision_hash[:24]}",
            "proposal_id": proposal["proposal_id"],
            "proposal_fingerprint": proposal["fingerprint"],
            "incident_id": proposal["incident_id"],
            "service": proposal["service"],
            "decision": response["decision"],
            "operator_id": response["operator_id"],
            "decided_at": decided_at,
        },
        proposal,
        state,
    )
    return {
        "approval_decision": decision,
        "remediation_outcome": (
            "approved" if decision["decision"] == "approve" else "rejected"
        ),
    }
