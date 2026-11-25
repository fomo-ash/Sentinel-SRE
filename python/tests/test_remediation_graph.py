"""Approval-gated simulated execution and replay through the graph."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from langgraph.types import Command

from sentinel.graph import build_graph
from sentinel.nodes.remediate import build_remediate_node
from sentinel.tools.remediation import RemediationClientError

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "services/mock-service/internal/incidents/fixtures"
)


class FixtureClient:
    def read(self, kind, incident_id, service, *, limit=None):
        snapshots = json.loads((_FIXTURE_DIR / f"{kind}_v1.json").read_text())
        snapshot = next(
            item for item in snapshots if item["incident_id"] == incident_id
        )
        assert snapshot["service"] == service
        return snapshot


class FakeActionClient:
    def __init__(self, *, lookup_error=None, execute_error=None, receipt_change=None):
        self.ledger = {}
        self.calls = []
        self.lookup_error = lookup_error
        self.execute_error = execute_error
        self.receipt_change = receipt_change

    def get_receipt(self, action_id):
        self.calls.append(("get", action_id))
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.ledger.get(action_id)

    def execute(self, proposal, approval, action_id):
        self.calls.append(("post", action_id))
        assert approval["decision"] == "approve"
        assert approval["proposal_fingerprint"] == proposal["fingerprint"]
        if self.execute_error is not None:
            raise self.execute_error
        receipt = {
            "action_id": action_id,
            "incident_id": proposal["incident_id"],
            "service": proposal["service"],
            "action": proposal["action"],
            "proposal_fingerprint": proposal["fingerprint"],
            "approval_reference": approval["decision_id"],
            "status": "applied",
            "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if self.receipt_change is not None:
            self.receipt_change(receipt)
        self.ledger[action_id] = receipt
        return receipt


def _start(*, action_client=None, mode="simulated", incident_id="diag-db-auth"):
    action_client = action_client or FakeActionClient()
    graph = build_graph(
        diagnostic_client=FixtureClient(), remediation_client=action_client
    )
    config = {
        "configurable": {
            "thread_id": "task-six-review",
            "investigation_mode": "simulated",
            "remediation_mode": mode,
        }
    }
    pending = graph.invoke(
        {
            "incident": {
                "id": incident_id,
                "service": "api",
                "summary": "Investigate the synthetic incident.",
            }
        },
        config,
    )
    return graph, action_client, config, pending


def _response(pending, decision="approve"):
    prompt = pending["__interrupt__"][0].value
    proposal = prompt["proposal"]
    return {
        "thread_id": prompt["thread_id"],
        "proposal_id": proposal["proposal_id"],
        "proposal_fingerprint": proposal["fingerprint"],
        "incident_id": proposal["incident_id"],
        "service": proposal["service"],
        "decision": decision,
        "operator_id": "local-reviewer",
    }


def test_pending_and_rejected_decisions_never_call_action_client():
    graph, client, config, pending = _start()
    assert pending["__interrupt__"]
    assert graph.get_state(config).values["approval_decision"] is None
    assert client.calls == []
    rejected = graph.invoke(Command(resume=_response(pending, "reject")), config)
    assert rejected["remediation_outcome"] == "rejected"
    assert rejected["execution_receipt"] is None
    assert client.calls == []


def test_approved_decision_is_checkpointed_before_one_action_call():
    graph, client, config, pending = _start()
    initial_severity = graph.get_state(config).values["final_severity"]
    approved = graph.invoke(Command(resume=_response(pending)), config)
    receipt = approved["execution_receipt"]
    assert approved["remediation_outcome"] == "verification_failed"
    assert approved["execution_failure_reason"] is None
    assert receipt["status"] == "applied"
    assert receipt["approval_reference"] == approved["approval_decision"]["decision_id"]
    assert receipt["action_id"].startswith("action-")
    assert [name for name, _ in client.calls] == ["get", "post"]
    assert client.calls[0][1] == client.calls[1][1] == receipt["action_id"]
    assert any(
        snapshot.next == ("remediate",)
        and snapshot.values["approval_decision"] == approved["approval_decision"]
        and snapshot.values["execution_receipt"] is None
        for snapshot in graph.get_state_history(config)
    )
    assert graph.get_state(config).next == ()
    assert approved["verification_result"]["failure_reason"] == "stale"
    assert approved["final_severity"] == initial_severity


def test_ineligible_incident_and_approval_only_mode_never_execute():
    graph, client, config, result = _start(incident_id="diag-healthy")
    assert "__interrupt__" not in result
    assert result["remediation_outcome"] == "no_action"
    assert client.calls == []
    graph, client, config, pending = _start(mode="approval")
    approved = graph.invoke(Command(resume=_response(pending)), config)
    assert approved["remediation_outcome"] == "approved"
    assert approved["execution_receipt"] is None
    assert client.calls == []


def test_default_graph_mode_never_calls_action_client():
    client = FakeActionClient()
    graph = build_graph(remediation_client=client)
    result = graph.invoke(
        {"incident": {"id": "diag-db-auth", "service": "api", "summary": "test"}},
        {"configurable": {"thread_id": "default-off"}},
    )
    assert result["action_proposal"] is None
    assert result["approval_decision"] is None
    assert client.calls == []


def test_missing_action_client_and_invalid_mode_fail_before_approval():
    config = {
        "configurable": {
            "thread_id": "missing-action-client",
            "investigation_mode": "simulated",
            "remediation_mode": "simulated",
        }
    }
    incident = {"incident": {"id": "diag-db-auth", "service": "api", "summary": "test"}}
    graph = build_graph(diagnostic_client=FixtureClient())
    with pytest.raises(ValueError, match="injected action client"):
        graph.invoke(incident, config)
    config["configurable"]["remediation_mode"] = "unknown"
    with pytest.raises(ValueError, match="Unsupported remediation mode"):
        graph.invoke(incident, config)


def test_approval_only_checkpoint_cannot_switch_to_execution_on_resume():
    graph, client, config, pending = _start(mode="approval")
    changed = {
        "configurable": {**config["configurable"], "remediation_mode": "simulated"}
    }
    with pytest.raises(ValueError, match="mode from the checkpoint"):
        graph.invoke(Command(resume=_response(pending)), changed)
    assert graph.get_state(config).values["approval_decision"] is None
    assert client.calls == []


@pytest.mark.parametrize("category", ["rejected", "conflict"])
def test_definite_action_rejection_is_terminal_failure(category):
    client = FakeActionClient(execute_error=RemediationClientError(category))
    graph, client, config, pending = _start(action_client=client)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "execution_failed"
    assert result["execution_failure_reason"] == category
    assert result["execution_receipt"] is None
    assert [name for name, _ in client.calls] == ["get", "post"]


@pytest.mark.parametrize("category", ["execution_unknown", "timeout", "unavailable"])
def test_ambiguous_action_failure_is_unknown_without_retry(category):
    client = FakeActionClient(execute_error=RemediationClientError(category))
    graph, client, config, pending = _start(action_client=client)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "execution_unknown"
    assert result["execution_receipt"] is None
    assert result["execution_failure_reason"] == (
        None if category == "execution_unknown" else category
    )
    assert [name for name, _ in client.calls] == ["get", "post"]


def test_unavailable_ledger_does_not_send_a_post():
    client = FakeActionClient(lookup_error=RemediationClientError("unavailable"))
    graph, client, config, pending = _start(action_client=client)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "execution_unknown"
    assert result["execution_failure_reason"] == "unavailable"
    assert [name for name, _ in client.calls] == ["get"]


def test_invalid_ledger_receipt_does_not_send_a_post():
    class InvalidLedgerClient(FakeActionClient):
        def get_receipt(self, action_id):
            self.calls.append(("get", action_id))
            return {"action_id": action_id, "status": "applied"}

    client = InvalidLedgerClient()
    graph, client, config, pending = _start(action_client=client)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "execution_unknown"
    assert result["execution_failure_reason"] == "invalid_response"
    assert [name for name, _ in client.calls] == ["get"]


@pytest.mark.parametrize(
    "change",
    [
        lambda receipt: receipt.update(approval_reference="decision-other"),
        lambda receipt: receipt.update(proposal_fingerprint="b" * 64),
        lambda receipt: receipt.update(action_id="action-other"),
    ],
)
def test_wrong_receipt_never_claims_applied_action(change):
    client = FakeActionClient(receipt_change=change)
    graph, client, config, pending = _start(action_client=client)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "execution_unknown"
    assert result["execution_receipt"] is None
    assert result["execution_failure_reason"] == "invalid_response"


def test_repeated_resume_cannot_send_a_second_action():
    graph, client, config, pending = _start()
    response = _response(pending)
    first = graph.invoke(Command(resume=response), config)
    graph.invoke(Command(resume=response), config)
    assert [name for name, _ in client.calls] == ["get", "post"]
    assert (
        graph.get_state(config).values["execution_receipt"]
        == first["execution_receipt"]
    )


def test_replay_after_lost_response_recovers_ledger_receipt_without_post():
    class LostResponseClient(FakeActionClient):
        def execute(self, proposal, approval, action_id):
            super().execute(proposal, approval, action_id)
            raise RemediationClientError("execution_unknown")

    client = LostResponseClient()
    graph, client, config, pending = _start(action_client=client)
    first = graph.invoke(Command(resume=_response(pending)), config)
    assert first["remediation_outcome"] == "execution_unknown"
    assert first["execution_receipt"] is None
    approved_checkpoint = next(
        snapshot
        for snapshot in graph.get_state_history(config)
        if snapshot.next == ("remediate",)
    )
    replay_config = {
        "configurable": {
            **config["configurable"],
            **approved_checkpoint.config["configurable"],
        }
    }
    recovered = graph.invoke(None, replay_config)
    assert recovered["execution_receipt"]["status"] == "applied"
    assert recovered["execution_receipt"]["action_id"] == client.calls[0][1]
    assert [name for name, _ in client.calls] == ["get", "post", "get"]


def test_checkpointed_receipt_is_reused_without_any_client_call():
    graph, client, config, pending = _start()
    result = graph.invoke(Command(resume=_response(pending)), config)
    before = list(client.calls)
    replay_state = {**result, "remediation_outcome": "approved"}
    replay = build_remediate_node(client)(replay_state, config)
    assert replay["execution_receipt"] == result["execution_receipt"]
    assert client.calls == before


@pytest.mark.parametrize("tamper", ["reject", "fingerprint", "evidence"])
def test_changed_checkpoint_cannot_authorize_another_action(tamper):
    graph, client, config, pending = _start()
    result = graph.invoke(Command(resume=_response(pending)), config)
    changed = dict(result)
    changed["execution_receipt"] = None
    changed["approval_decision"] = dict(result["approval_decision"])
    if tamper == "reject":
        changed["approval_decision"]["decision"] = "reject"
    elif tamper == "fingerprint":
        changed["approval_decision"]["proposal_fingerprint"] = "b" * 64
    else:
        changed["evidence"] = []
    before = list(client.calls)
    outcome = build_remediate_node(client)(changed, config)
    assert outcome["remediation_outcome"] == "execution_failed"
    assert outcome["execution_failure_reason"] == "rejected"
    assert client.calls == before
