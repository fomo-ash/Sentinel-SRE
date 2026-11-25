"""Execute one approved local action after its decision has been checkpointed."""

import hashlib
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from sentinel.nodes.propose_remediation import propose_remediation
from sentinel.schemas import (
    ActionProposal,
    ApprovalDecision,
    ExecutionFailure,
    ExecutionReceipt,
    InvestigationState,
    validate_action_proposal,
    validate_approval_decision,
    validate_execution_receipt,
)
from sentinel.tools.remediation import RemediationClient, RemediationClientError


def _action_id(thread_id: str, proposal: ActionProposal) -> str:
    """Reuse the same synthetic ID on every replay of this thread and proposal."""
    encoded = (
        f"{thread_id}\0{proposal['proposal_id']}\0{proposal['fingerprint']}".encode()
    )
    return f"action-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _check_receipt(
    value: object,
    action_id: str,
    proposal: ActionProposal,
    approval: ApprovalDecision,
) -> ExecutionReceipt:
    """Bind even a standalone ledger lookup to the checkpointed decision."""
    receipt = validate_execution_receipt(value, proposal, approval)
    if receipt["action_id"] != action_id:
        raise ValueError("Receipt action ID does not match this workflow")
    applied = datetime.fromisoformat(receipt["applied_at"].replace("Z", "+00:00"))
    decided = datetime.fromisoformat(approval["decided_at"].replace("Z", "+00:00"))
    if applied < decided:
        raise ValueError("Receipt predates approval")
    return receipt


def _unknown(reason: ExecutionFailure | None = None) -> dict:
    # A read or POST may have reached the local service. Never send another POST.
    return {
        "execution_receipt": None,
        "execution_failure_reason": reason,
        "remediation_outcome": "execution_unknown",
    }


def build_remediate_node(client: RemediationClient | None):
    """Bind the injected client without constructing one for default graph runs."""

    def remediate(state: InvestigationState, config: RunnableConfig) -> dict:
        configurable = config.get("configurable", {})
        if (
            configurable.get("remediation_mode") != "simulated"
            or state.get("approval_mode") != "simulated"
            or client is None
        ):
            raise ValueError("Simulated remediation requires an injected action client")

        # Recompute the pure policy proposal so a changed checkpoint cannot
        # substitute a different action or evidence after the human decision.
        try:
            proposal = validate_action_proposal(state["action_proposal"], state)
            if propose_remediation(state)["action_proposal"] != proposal:
                raise ValueError("Proposal no longer matches current evidence")
            approval = validate_approval_decision(
                state["approval_decision"], proposal, state
            )
            if (
                state.get("remediation_outcome") != "approved"
                or approval["decision"] != "approve"
                or datetime.fromisoformat(approval["decided_at"].replace("Z", "+00:00"))
                > datetime.now(UTC)
            ):
                raise ValueError("No valid recorded approval")
            thread_id = configurable.get("thread_id")
            if (
                not isinstance(thread_id, str)
                or not thread_id.strip()
                or len(thread_id) > 128
            ):
                raise ValueError("Missing workflow thread")
        except (KeyError, TypeError, ValueError, ValidationError):
            return {
                "execution_receipt": None,
                "execution_failure_reason": "rejected",
                "remediation_outcome": "execution_failed",
            }

        action_id = _action_id(thread_id, proposal)
        checkpoint_receipt = state.get("execution_receipt")
        if checkpoint_receipt is not None:
            try:
                receipt = _check_receipt(
                    checkpoint_receipt, action_id, proposal, approval
                )
            except (KeyError, TypeError, ValueError, ValidationError):
                pass
            else:
                return {
                    "execution_receipt": receipt,
                    "execution_failure_reason": None,
                    "remediation_outcome": "approved",
                }

        # This read is essential on replay: the POST may have applied its
        # effect before the graph checkpointed the returned receipt.
        try:
            prior = client.get_receipt(action_id)
            if prior is not None:
                receipt = _check_receipt(prior, action_id, proposal, approval)
                return {
                    "execution_receipt": receipt,
                    "execution_failure_reason": None,
                    "remediation_outcome": "approved",
                }
        except RemediationClientError as exc:
            reason = exc.category if exc.category != "execution_unknown" else None
            return _unknown(reason)
        except Exception:
            return _unknown("invalid_response")
        if checkpoint_receipt is not None:
            return _unknown("invalid_response")

        try:
            receipt = client.execute(proposal, approval, action_id)
            receipt = _check_receipt(receipt, action_id, proposal, approval)
        except RemediationClientError as exc:
            if exc.category in ("rejected", "conflict"):
                return {
                    "execution_receipt": None,
                    "execution_failure_reason": exc.category,
                    "remediation_outcome": "execution_failed",
                }
            reason = exc.category if exc.category != "execution_unknown" else None
            return _unknown(reason)
        except Exception:
            return _unknown("invalid_response")
        return {
            "execution_receipt": receipt,
            "execution_failure_reason": None,
            "remediation_outcome": "approved",
        }

    return remediate
