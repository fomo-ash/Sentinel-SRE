"""Propose one synthetic action from validated current incident evidence."""

import hashlib
import json

from pydantic import ValidationError

from sentinel.nodes.diagnose import diagnose_baseline
from sentinel.schemas import (
    InvestigationState,
    NoActionReason,
    validate_action_proposal,
    validate_diagnosis,
)

_ACTION = "restore_simulated_db_login"
_REASON = (
    "Current API health and log observations support database login rejection. "
    "This proposes a local synthetic restoration for human review."
)


def _no_action(reason: NoActionReason) -> dict:
    return {
        "action_proposal": None,
        "no_action_reason": reason,
        "remediation_outcome": "no_action",
    }


def propose_remediation(state: InvestigationState) -> dict:
    """Return one evidence-bound proposal or a fixed abstention reason.

    This node is pure. It does not request approval or call a mutation client.
    """
    incident = state["incident"]
    if (incident["id"], incident["service"]) != ("diag-db-auth", "api"):
        return _no_action("ineligible_incident")

    if (
        state.get("investigation_stop_reason") != "checks_complete"
        or state.get("diagnostic_failures")
        or not {"health", "metrics", "logs"} <= set(state.get("completed_checks", []))
    ):
        return _no_action("incomplete_diagnostics")

    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        return _no_action("unsupported_diagnosis")
    try:
        diagnosis = validate_diagnosis(diagnosis, state)
    except (ValidationError, ValueError, KeyError, TypeError):
        return _no_action("unsupported_diagnosis")
    if (
        diagnosis["status"] != "supported"
        or diagnosis["source"] not in ("deterministic", "llm")
        or state.get("diagnosis_source") != diagnosis["source"]
    ):
        return _no_action("unsupported_diagnosis")

    try:
        policy_diagnosis = diagnose_baseline(state)["diagnosis"]
    except (ValidationError, ValueError, KeyError, TypeError):
        return _no_action("unsupported_evidence")
    if (
        policy_diagnosis["status"] != "supported"
        or diagnosis["hypotheses"] != policy_diagnosis["hypotheses"]
    ):
        return _no_action("unsupported_evidence")

    evidence_ids = sorted(policy_diagnosis["hypotheses"][0]["supporting_evidence_ids"])
    current = {
        item["id"]: item
        for item in state.get("evidence", [])
        if item["incident_id"] == incident["id"]
        and item["service"] == incident["service"]
    }
    fingerprint_payload = {
        "incident_id": incident["id"],
        "service": incident["service"],
        "action": _ACTION,
        "current_evidence": [
            {
                "id": current[evidence_id]["id"],
                "kind": current[evidence_id]["kind"],
                "source_ref": current[evidence_id]["source_ref"],
                "observed_at": current[evidence_id]["observed_at"],
                "observation": current[evidence_id]["observation"],
            }
            for evidence_id in evidence_ids
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    proposal = {
        "proposal_id": f"proposal-{fingerprint[:24]}",
        "incident_id": incident["id"],
        "service": incident["service"],
        "action": _ACTION,
        "reason": _REASON,
        "current_evidence_ids": evidence_ids,
        "fingerprint": fingerprint,
    }
    return {
        "action_proposal": validate_action_proposal(proposal, state),
        "no_action_reason": None,
        "remediation_outcome": None,
    }
