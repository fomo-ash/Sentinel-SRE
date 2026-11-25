"""Summarize each completed approval flow without inventing recovery."""

from sentinel.schemas import (
    InvestigationState,
    validate_action_proposal,
    validate_remediation_report,
)


def mark_proposal_ready(state: InvestigationState) -> dict:
    """Give the inspection-only proposal a truthful terminal outcome."""
    if (
        state.get("action_proposal") is None
        or state.get("approval_decision") is not None
    ):
        raise ValueError("Proposal-only report requires an unapproved proposal")
    validate_action_proposal(state["action_proposal"], state)
    return {"remediation_outcome": "proposal_ready"}


def report_remediation(state: InvestigationState) -> dict:
    """Produce one bounded report from checkpointed outcomes and evidence."""
    outcome = state.get("remediation_outcome")
    messages = {
        "no_action": (
            "No synthetic action was approved or applied.",
            ["Review the diagnosis and missing evidence before proposing an action."],
        ),
        "proposal_ready": (
            "A synthetic action is proposed; no approval or action occurred.",
            ["Review the exact proposal before starting an approval workflow."],
        ),
        "rejected": (
            "The proposed synthetic action was rejected; no action was applied.",
            ["Record any operator follow-up outside this local simulation."],
        ),
        "approved": (
            "Approval was recorded in approval-only mode; no action was applied.",
            ["Start a new simulated workflow to review execution."],
        ),
        "execution_failed": (
            "The synthetic action was not confirmed after execution failed.",
            ["Inspect the local action ledger and failure category."],
        ),
        "execution_unknown": (
            "The synthetic action outcome is unknown; application was not confirmed.",
            ["Inspect the local action ledger; do not retry with a new action ID."],
        ),
        "verification_failed": (
            "The action was applied, but fresh health did not confirm recovery.",
            ["Investigate the post-action health and unresolved impact."],
        ),
        "verification_unknown": (
            "The synthetic action was applied, but recovery could not be determined.",
            ["Obtain a fresh incident health observation before claiming recovery."],
        ),
        "verified": (
            "Fresh incident health verified the applied synthetic action.",
            ["Review the underlying cause of the original database login rejection."],
        ),
    }
    if outcome not in messages:
        raise ValueError("A terminal remediation outcome is required for reporting")
    summary, follow_up = messages[outcome]
    proposal = state.get("action_proposal")
    approval = state.get("approval_decision")
    receipt = state.get("execution_receipt")
    verification = state.get("verification_result")
    diagnosis = state.get("diagnosis")
    report = validate_remediation_report(
        {
            "incident_id": state["incident"]["id"],
            "service": state["incident"]["service"],
            "outcome": outcome,
            "final_severity": state["final_severity"],
            "diagnosis_status": diagnosis["status"] if diagnosis else None,
            "diagnosis": diagnosis,
            "summary": summary,
            "proposal_id": proposal["proposal_id"] if proposal else None,
            "action_proposal": proposal,
            "decision_id": approval["decision_id"] if approval else None,
            "approval_decision": approval,
            "action_id": receipt["action_id"] if receipt else None,
            "execution_receipt": receipt,
            "verification_status": verification["status"] if verification else None,
            "verification_result": verification,
            "follow_up": follow_up,
        },
        state,
    )
    return {"remediation_report": report}
