"""Loopback-only mock action client and lost-response reconciliation."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from sentinel.tools.remediation import (
    HTTPRemediationClient,
    RemediationClient,
    RemediationClientError,
)

_ACTION_ID = "action-test-one"


def _records():
    fingerprint = "a" * 64
    decided_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(
        timespec="seconds"
    )
    proposal = {
        "proposal_id": "proposal-test-one",
        "incident_id": "diag-db-auth",
        "service": "api",
        "action": "restore_simulated_db_login",
        "reason": "Current health and logs show database login rejection.",
        "current_evidence_ids": [
            "diag-db-auth:health:database_auth",
            "diag-db-auth:log:db-auth-rejected",
        ],
        "fingerprint": fingerprint,
    }
    approval = {
        "decision_id": "decision-test-one",
        "proposal_id": proposal["proposal_id"],
        "proposal_fingerprint": fingerprint,
        "incident_id": "diag-db-auth",
        "service": "api",
        "decision": "approve",
        "operator_id": "local-reviewer",
        "decided_at": decided_at,
    }
    receipt = {
        "action_id": _ACTION_ID,
        "incident_id": "diag-db-auth",
        "service": "api",
        "action": proposal["action"],
        "proposal_fingerprint": fingerprint,
        "approval_reference": approval["decision_id"],
        "status": "applied",
        "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return proposal, approval, receipt


def _client(handler):
    return HTTPRemediationClient("diag-db-auth", transport=httpx.MockTransport(handler))


def test_one_post_sends_only_recorded_approval_and_validates_receipt():
    proposal, approval, receipt = _records()
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/incidents/diag-db-auth/remediations"
        payload = json.loads(request.content)
        assert payload == {
            "action_id": _ACTION_ID,
            "incident_id": "diag-db-auth",
            "service": "api",
            "action": "restore_simulated_db_login",
            "proposal_id": proposal["proposal_id"],
            "proposal_fingerprint": proposal["fingerprint"],
            "approval": approval,
        }
        return httpx.Response(201, json=receipt)

    with _client(handler) as client:
        assert client.execute(proposal, approval, _ACTION_ID) == receipt
    assert len(seen) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda proposal, approval: approval.update(decision="reject"),
        lambda proposal, approval: approval.update(proposal_fingerprint="b" * 64),
        lambda proposal, approval: approval.update(incident_id="diag-other"),
        lambda proposal, approval: approval.update(operator_id="  "),
        lambda proposal, approval: proposal.update(action="restart_service"),
        lambda proposal, approval: proposal.update(service="worker"),
        lambda proposal, approval: proposal.update(
            current_evidence_ids=["hist-db-auth"]
        ),
    ],
)
def test_invalid_or_unapproved_input_never_sends_http(change):
    proposal, approval, _ = _records()
    change(proposal, approval)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201)

    with _client(handler) as client:
        with pytest.raises((ValueError, ValidationError)):
            client.execute(proposal, approval, _ACTION_ID)
    assert calls == []


def test_invalid_action_id_never_enters_a_url():
    proposal, approval, _ = _records()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201)

    with _client(handler) as client:
        with pytest.raises(ValueError, match="action_id"):
            client.execute(proposal, approval, "../other")
        with pytest.raises(ValueError, match="action_id"):
            client.get_receipt("../other")
    assert calls == []


@pytest.mark.parametrize(
    "address",
    [
        "https://127.0.0.1:8080",
        "http://example.com:8080",
        "http://localhost:8080",
        "http://127.0.0.1:8080/other",
        "http://user:pass@127.0.0.1:8080",
        "http://127.0.0.1:8080?target=other",
    ],
)
def test_constructor_rejects_unsafe_origins(address):
    with pytest.raises(ValueError, match="loopback HTTP origin"):
        HTTPRemediationClient("diag-db-auth", address)


def test_constructor_requires_only_eligible_synthetic_incident():
    with pytest.raises(ValueError, match="diag-db-auth"):
        HTTPRemediationClient("diag-other")


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan")])
def test_constructor_rejects_invalid_timeouts(timeout):
    with pytest.raises(ValueError, match="timeout"):
        HTTPRemediationClient("diag-db-auth", timeout=timeout)


def test_timeout_reconciles_with_one_get_and_no_second_post():
    proposal, approval, receipt = _records()
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("lost response", request=request)
        assert request.url.path.endswith("/" + _ACTION_ID)
        return httpx.Response(200, json=receipt)

    with _client(handler) as client:
        assert client.execute(proposal, approval, _ACTION_ID) == receipt
    assert methods == ["POST", "GET"]


def test_server_error_reconciles_by_receipt_lookup():
    proposal, approval, receipt = _records()
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(503)
        return httpx.Response(200, json=receipt)

    with _client(handler) as client:
        assert client.execute(proposal, approval, _ACTION_ID) == receipt
    assert methods == ["POST", "GET"]


@pytest.mark.parametrize("get_status", [404, 503])
def test_unresolved_post_is_unknown_and_never_retried(get_status):
    proposal, approval, _ = _records()
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            raise httpx.ConnectError("response lost", request=request)
        return httpx.Response(get_status)

    with _client(handler) as client:
        with pytest.raises(RemediationClientError) as error:
            client.execute(proposal, approval, _ACTION_ID)
    assert error.value.category == "execution_unknown"
    assert str(error.value) == "execution_unknown"
    assert methods == ["POST", "GET"]


@pytest.mark.parametrize(
    ("post_status", "category"),
    [
        (409, "conflict"),
        (400, "rejected"),
        (401, "rejected"),
        (403, "rejected"),
        (422, "rejected"),
    ],
)
def test_definite_post_rejections_do_not_trigger_lookup(post_status, category):
    proposal, approval, _ = _records()
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(post_status, json={"error": "fixed_server_code"})

    with _client(handler) as client:
        with pytest.raises(RemediationClientError) as error:
            client.execute(proposal, approval, _ACTION_ID)
    assert error.value.category == category
    assert methods == ["POST"]


@pytest.mark.parametrize("response_body", [{"status": "applied"}, {"error": "bad"}])
def test_malformed_success_receipt_reconciles_or_reports_unknown(response_body):
    proposal, approval, _ = _records()
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json=response_body)
        return httpx.Response(404)

    with _client(handler) as client:
        with pytest.raises(RemediationClientError) as error:
            client.execute(proposal, approval, _ACTION_ID)
    assert error.value.category == "execution_unknown"
    assert methods == ["POST", "GET"]


def test_mismatched_receipt_is_never_accepted_even_after_lookup():
    proposal, approval, receipt = _records()
    receipt["approval_reference"] = "decision-other"
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(201 if request.method == "POST" else 200, json=receipt)

    with _client(handler) as client:
        with pytest.raises(RemediationClientError) as error:
            client.execute(proposal, approval, _ACTION_ID)
    assert error.value.category == "execution_unknown"
    assert methods == ["POST", "GET"]


def test_receipt_predating_approval_is_not_accepted():
    proposal, approval, receipt = _records()
    receipt["applied_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(201 if request.method == "POST" else 200, json=receipt)

    with _client(handler) as client:
        with pytest.raises(RemediationClientError) as error:
            client.execute(proposal, approval, _ACTION_ID)
    assert error.value.category == "execution_unknown"
    assert methods == ["POST", "GET"]


def test_direct_receipt_lookup_validates_identity_and_failure_categories():
    _, _, receipt = _records()
    with _client(lambda request: httpx.Response(200, json=receipt)) as client:
        assert client.get_receipt(_ACTION_ID) == receipt
    with _client(lambda request: httpx.Response(404)) as client:
        assert client.get_receipt(_ACTION_ID) is None
    wrong = {**receipt, "service": "worker"}
    with _client(lambda request: httpx.Response(200, json=wrong)) as client:
        with pytest.raises(RemediationClientError) as error:
            client.get_receipt(_ACTION_ID)
    assert error.value.category == "invalid_response"


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "unavailable"),
    ],
)
def test_lookup_transport_failures_have_fixed_categories(failure, category):
    def handler(request):
        raise failure("synthetic transport error", request=request)

    with _client(handler) as client:
        with pytest.raises(RemediationClientError) as error:
            client.get_receipt(_ACTION_ID)
    assert error.value.category == category
    assert str(error.value) == category


def test_fake_client_can_implement_protocol_without_network():
    _, _, receipt = _records()

    class FakeClient:
        def execute(self, proposal, approval, action_id):
            assert approval["decision"] == "approve"
            return receipt

        def get_receipt(self, action_id):
            return receipt if action_id == _ACTION_ID else None

    client: RemediationClient = FakeClient()
    proposal, approval, _ = _records()
    assert client.execute(proposal, approval, _ACTION_ID) == receipt
    assert client.get_receipt("action-missing") is None
