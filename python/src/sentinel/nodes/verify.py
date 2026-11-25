"""Verify a synthetic action with one fresh incident health observation."""

from datetime import datetime

from pydantic import ValidationError

from sentinel.schemas import (
    ExecutionReceipt,
    InvestigationState,
    VerificationFailure,
    validate_action_proposal,
    validate_approval_decision,
    validate_execution_receipt,
    validate_verification_result,
)
from sentinel.tools.diagnostics import (
    DiagnosticClient,
    DiagnosticReadError,
    validate_diagnostic_response,
)

_RESTORED_CHECK = "db_login_restored"
_PRE_ACTION_HEALTH = "diag-db-auth:health:database_auth"


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Observation requires a timezone")
    return parsed


def _result(
    receipt: ExecutionReceipt,
    status: str,
    failure: VerificationFailure | None,
    observation_id: str | None = None,
    observed_at: str | None = None,
) -> dict:
    result = validate_verification_result(
        {
            "action_id": receipt["action_id"],
            "incident_id": receipt["incident_id"],
            "service": receipt["service"],
            "status": status,
            "observation_id": observation_id,
            "observed_at": observed_at,
            "failure_reason": failure,
        },
        receipt,
    )
    outcome = {
        "verified": "verified",
        "failed": "verification_failed",
        "unknown": "verification_unknown",
    }[status]
    return {"verification_result": result, "remediation_outcome": outcome}


def build_verify_node(client: DiagnosticClient | None):
    """Keep post-action health reads behind the injected diagnostic client."""

    def verify(state: InvestigationState) -> dict:
        if client is None:
            raise ValueError("Verification requires an injected diagnostic client")
        if state.get("remediation_outcome") != "approved":
            raise ValueError("Verification requires an applied action")

        try:
            proposal = validate_action_proposal(state["action_proposal"], state)
            approval = validate_approval_decision(
                state["approval_decision"], proposal, state
            )
            receipt = validate_execution_receipt(
                state["execution_receipt"], proposal, approval
            )
            if _time(receipt["applied_at"]) < _time(approval["decided_at"]):
                raise ValueError("Receipt predates recorded approval")
        except (KeyError, TypeError, ValueError, ValidationError):
            # A malformed receipt cannot establish that an action was applied.
            return {
                "execution_receipt": None,
                "execution_failure_reason": "invalid_response",
                "verification_result": None,
                "remediation_outcome": "execution_unknown",
            }

        health_evidence = [
            item
            for item in state.get("evidence", [])
            if isinstance(item, dict)
            and item.get("id") == _PRE_ACTION_HEALTH
            and item.get("incident_id") == receipt["incident_id"]
            and item.get("service") == receipt["service"]
            and item.get("kind") == "health"
        ]
        try:
            if len(health_evidence) != 1:
                raise ValueError("Missing pre-action health observation")
            pre_action_time = _time(health_evidence[0]["observed_at"])
            applied_time = _time(receipt["applied_at"])
        except (KeyError, TypeError, ValueError):
            return _result(receipt, "unknown", "invalid_response")
        if applied_time <= pre_action_time:
            return _result(receipt, "unknown", "stale")

        try:
            raw = client.read("health", receipt["incident_id"], receipt["service"])
            snapshot = validate_diagnostic_response(
                "health", receipt["incident_id"], receipt["service"], raw
            )
        except DiagnosticReadError as exc:
            return _result(receipt, "unknown", exc.category)
        except Exception:
            return _result(receipt, "unknown", "invalid_response")

        observed_at = snapshot["observed_at"]
        restored = next(
            (check for check in snapshot["checks"] if check["id"] == _RESTORED_CHECK),
            None,
        )
        observation_id = (
            f"{receipt['incident_id']}:health:{_RESTORED_CHECK}"
            if restored is not None
            else None
        )
        if _time(observed_at) < applied_time or _time(observed_at) <= pre_action_time:
            return _result(receipt, "failed", "stale", observation_id, observed_at)
        if restored is None:
            return _result(receipt, "unknown", "missing_observation", None, observed_at)
        if snapshot["status"] != "healthy" or any(
            check["status"] != "healthy" for check in snapshot["checks"]
        ):
            return _result(receipt, "failed", "unhealthy", observation_id, observed_at)
        return _result(receipt, "verified", None, observation_id, observed_at)

    return verify
