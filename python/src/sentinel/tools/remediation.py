"""Validated, loopback-only client for one approved synthetic action."""

import math
import re
from datetime import UTC, datetime
from typing import Literal, Protocol

import httpx
from pydantic import TypeAdapter, ValidationError

from sentinel.config import load_settings, validate_mock_service_origin
from sentinel.schemas import (
    ActionProposal,
    ApprovalDecision,
    ExecutionReceipt,
    validate_execution_receipt,
)

RemediationFailureCategory = Literal[
    "timeout",
    "unavailable",
    "invalid_response",
    "conflict",
    "rejected",
    "execution_unknown",
]

_ACTION_ID = re.compile(r"^action-[a-z0-9]+(?:-[a-z0-9]+)*$")
_PROPOSAL_ID = re.compile(r"^proposal-[a-z0-9]+(?:-[a-z0-9]+)*$")
_DECISION_ID = re.compile(r"^decision-[a-z0-9]+(?:-[a-z0-9]+)*$")
_ACTION = "restore_simulated_db_login"
_INCIDENT_ID = "diag-db-auth"
_SERVICE = "api"
_EVIDENCE_IDS = {
    "diag-db-auth:health:database_auth",
    "diag-db-auth:log:db-auth-rejected",
}
_proposal_adapter = TypeAdapter(ActionProposal)
_approval_adapter = TypeAdapter(ApprovalDecision)
_receipt_adapter = TypeAdapter(ExecutionReceipt)


class RemediationClientError(Exception):
    """A fixed outcome category; raw network details never enter graph state."""

    def __init__(self, category: RemediationFailureCategory):
        self.category = category
        super().__init__(category)


class RemediationClient(Protocol):
    """Fake-friendly boundary for one action attempt and receipt lookup."""

    def execute(
        self, proposal: ActionProposal, approval: ApprovalDecision, action_id: str
    ) -> ExecutionReceipt: ...

    def get_receipt(self, action_id: str) -> ExecutionReceipt | None: ...


def _valid_action_id(action_id: str) -> bool:
    return (
        isinstance(action_id, str)
        and len(action_id) <= 128
        and _ACTION_ID.fullmatch(action_id) is not None
    )


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires a timezone")
    return parsed


def _validate_action(
    proposal: object, approval: object, action_id: str, incident_id: str
) -> tuple[ActionProposal, ApprovalDecision]:
    """Check the exact allowlist and recorded approval before any HTTP call."""
    if not _valid_action_id(action_id):
        raise ValueError("action_id must be a bounded synthetic action ID")
    proposal = _proposal_adapter.validate_python(proposal, strict=True)
    approval = _approval_adapter.validate_python(approval, strict=True)
    if (
        proposal["incident_id"] != incident_id
        or proposal["service"] != _SERVICE
        or proposal["action"] != _ACTION
        or len(proposal["proposal_id"]) > 128
        or _PROPOSAL_ID.fullmatch(proposal["proposal_id"]) is None
        or set(proposal["current_evidence_ids"]) != _EVIDENCE_IDS
        or len(proposal["current_evidence_ids"]) != len(_EVIDENCE_IDS)
    ):
        raise ValueError("proposal is outside the synthetic action allowlist")
    if (
        approval["decision"] != "approve"
        or approval["incident_id"] != proposal["incident_id"]
        or approval["service"] != proposal["service"]
        or approval["proposal_id"] != proposal["proposal_id"]
        or approval["proposal_fingerprint"] != proposal["fingerprint"]
        or len(approval["decision_id"]) > 128
        or _DECISION_ID.fullmatch(approval["decision_id"]) is None
        or not approval["operator_id"].strip()
        or approval["operator_id"] != approval["operator_id"].strip()
    ):
        raise ValueError("approved decision does not match the proposal")
    if _parse_time(approval["decided_at"]) > datetime.now(UTC):
        raise ValueError("approval decision time is in the future")
    return proposal, approval


class HTTPRemediationClient:
    """POST at most once, then reconcile uncertain results by a read-only GET."""

    def __init__(
        self,
        incident_id: str,
        base_url: str | None = None,
        *,
        timeout: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if incident_id != _INCIDENT_ID:
            raise ValueError("remediation client supports only diag-db-auth")
        address = base_url if base_url is not None else load_settings().mock_service_url
        validate_mock_service_origin(address)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        self._incident_id = incident_id
        self._client = httpx.Client(
            base_url=address,
            timeout=timeout,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HTTPRemediationClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _receipt_from_response(
        self, response: httpx.Response, action_id: str
    ) -> ExecutionReceipt:
        try:
            raw = response.json()
            receipt = _receipt_adapter.validate_python(raw, strict=True)
            _parse_time(receipt["applied_at"])
        except (ValueError, ValidationError, TypeError) as exc:
            raise RemediationClientError("invalid_response") from exc
        if (
            receipt["action_id"] != action_id
            or receipt["incident_id"] != self._incident_id
            or receipt["service"] != _SERVICE
            or receipt["action"] != _ACTION
        ):
            raise RemediationClientError("invalid_response")
        return receipt

    def get_receipt(self, action_id: str) -> ExecutionReceipt | None:
        """Read a prior result; 404 means this ID is not recorded locally."""
        if not _valid_action_id(action_id):
            raise ValueError("action_id must be a bounded synthetic action ID")
        try:
            response = self._client.get(
                f"/incidents/{self._incident_id}/remediations/{action_id}"
            )
        except httpx.TimeoutException as exc:
            raise RemediationClientError("timeout") from exc
        except httpx.RequestError as exc:
            raise RemediationClientError("unavailable") from exc
        if response.status_code == 404:
            return None
        if 500 <= response.status_code <= 599:
            raise RemediationClientError("unavailable")
        if response.status_code != 200:
            raise RemediationClientError("invalid_response")
        return self._receipt_from_response(response, action_id)

    def execute(
        self, proposal: ActionProposal, approval: ApprovalDecision, action_id: str
    ) -> ExecutionReceipt:
        """Send one approved mock action; never retry a possibly applied POST."""
        proposal, approval = _validate_action(
            proposal, approval, action_id, self._incident_id
        )
        payload = {
            "action_id": action_id,
            "incident_id": proposal["incident_id"],
            "service": proposal["service"],
            "action": proposal["action"],
            "proposal_id": proposal["proposal_id"],
            "proposal_fingerprint": proposal["fingerprint"],
            "approval": approval,
        }
        try:
            response = self._client.post(
                f"/incidents/{self._incident_id}/remediations", json=payload
            )
        except (httpx.TimeoutException, httpx.RequestError):
            return self._reconcile(action_id, proposal, approval)

        if response.status_code == 409:
            raise RemediationClientError("conflict")
        if response.status_code in (400, 401, 403, 404, 422):
            raise RemediationClientError("rejected")
        if response.status_code not in (200, 201):
            return self._reconcile(action_id, proposal, approval)
        try:
            receipt = self._receipt_from_response(response, action_id)
            return self._validated_receipt(receipt, proposal, approval)
        except (RemediationClientError, ValueError, ValidationError):
            return self._reconcile(action_id, proposal, approval)

    @staticmethod
    def _validated_receipt(
        receipt: ExecutionReceipt, proposal: ActionProposal, approval: ApprovalDecision
    ) -> ExecutionReceipt:
        receipt = validate_execution_receipt(receipt, proposal, approval)
        if _parse_time(receipt["applied_at"]) < _parse_time(approval["decided_at"]):
            raise RemediationClientError("invalid_response")
        return receipt

    def _reconcile(
        self, action_id: str, proposal: ActionProposal, approval: ApprovalDecision
    ) -> ExecutionReceipt:
        """Use one lookup to resolve an ambiguous POST without another effect."""
        try:
            receipt = self.get_receipt(action_id)
            if receipt is not None:
                return self._validated_receipt(receipt, proposal, approval)
        except (RemediationClientError, ValueError, ValidationError):
            pass
        raise RemediationClientError("execution_unknown")
