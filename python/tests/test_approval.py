"""Checkpointed approve/reject transitions without simulated action effects."""

import json
from pathlib import Path

import pytest
from langgraph.types import Command

from sentinel.graph import build_graph

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "services/mock-service/internal/incidents/fixtures"
)


class FixtureClient:
    def __init__(self):
        self.calls = []

    def read(self, kind, incident_id, service, *, limit=None):
        self.calls.append(kind)
        snapshots = json.loads((_FIXTURE_DIR / f"{kind}_v1.json").read_text())
        snapshot = next(
            item for item in snapshots if item["incident_id"] == incident_id
        )
        assert snapshot["service"] == service
        return snapshot


def _start_approval(incident_id="diag-db-auth", service="api", thread_id="review-1"):
    client = FixtureClient()
    graph = build_graph(diagnostic_client=client)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "investigation_mode": "simulated",
            "remediation_mode": "approval",
        }
    }
    result = graph.invoke(
        {
            "incident": {
                "id": incident_id,
                "service": service,
                "summary": "Investigate the synthetic incident.",
            }
        },
        config,
    )
    return graph, client, config, result


def _response(payload, decision="approve"):
    proposal = payload["proposal"]
    return {
        "thread_id": payload["thread_id"],
        "proposal_id": proposal["proposal_id"],
        "proposal_fingerprint": proposal["fingerprint"],
        "incident_id": proposal["incident_id"],
        "service": proposal["service"],
        "decision": decision,
        "operator_id": "local-reviewer",
    }


def test_approval_interrupt_shows_exact_proposal_and_checkpoints_pending_state():
    graph, client, config, result = _start_approval()
    assert len(result["__interrupt__"]) == 1
    payload = result["__interrupt__"][0].value
    checkpoint = graph.get_state(config)
    proposal = checkpoint.values["action_proposal"]
    assert payload["type"] == "remediation_approval"
    assert payload["thread_id"] == config["configurable"]["thread_id"]
    assert payload["proposal"] == proposal
    assert payload["current_evidence_ids"] == proposal["current_evidence_ids"]
    assert "synthetic action" in payload["warning"]
    assert checkpoint.values["remediation_outcome"] == "awaiting_approval"
    assert checkpoint.values["approval_decision"] is None
    assert checkpoint.values["execution_receipt"] is None
    assert checkpoint.next == ("approval",)
    assert client.calls == ["health", "metrics", "logs"]


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_same_thread_resume_records_human_decision_without_action(decision):
    graph, client, config, result = _start_approval(thread_id=f"review-{decision}")
    payload = result["__interrupt__"][0].value
    resumed = graph.invoke(Command(resume=_response(payload, decision)), config)
    recorded = resumed["approval_decision"]
    assert recorded["decision"] == decision
    assert recorded["proposal_id"] == payload["proposal"]["proposal_id"]
    assert recorded["proposal_fingerprint"] == payload["proposal"]["fingerprint"]
    assert recorded["operator_id"] == "local-reviewer"
    assert recorded["decision_id"].startswith("decision-")
    assert recorded["decided_at"].endswith("Z")
    assert resumed["remediation_outcome"] == (
        "approved" if decision == "approve" else "rejected"
    )
    assert resumed["execution_receipt"] is None
    assert resumed["verification_result"] is None
    assert graph.get_state(config).values == resumed
    assert graph.get_state(config).next == ()
    assert client.calls == ["health", "metrics", "logs"]


@pytest.mark.parametrize(
    "change",
    [
        lambda response: True,
        lambda response: {**response, "decision": "maybe"},
        lambda response: {
            key: value for key, value in response.items() if key != "operator_id"
        },
        lambda response: {**response, "operator_id": "  "},
        lambda response: {**response, "proposal_fingerprint": "b" * 64},
        lambda response: {**response, "incident_id": "diag-other"},
        lambda response: {**response, "thread_id": "wrong-thread"},
        lambda response: {**response, "extra": "approve anyway"},
    ],
)
def test_invalid_resume_does_not_record_approval(change):
    graph, _, config, result = _start_approval()
    response = _response(result["__interrupt__"][0].value)
    retry = graph.invoke(Command(resume=change(response)), config)
    assert retry["__interrupt__"][0].value["validation_error"] == (
        "invalid_approval_response"
    )
    checkpoint = graph.get_state(config)
    assert checkpoint.values["approval_decision"] is None
    assert checkpoint.values["execution_receipt"] is None
    assert checkpoint.values["remediation_outcome"] == "awaiting_approval"


def test_invalid_response_can_be_corrected_on_same_pending_thread():
    graph, _, config, result = _start_approval()
    response = _response(result["__interrupt__"][0].value)
    graph.invoke(Command(resume={**response, "proposal_id": "stale"}), config)
    resumed = graph.invoke(Command(resume=response), config)
    assert resumed["approval_decision"]["decision"] == "approve"
    assert resumed["execution_receipt"] is None


def test_three_invalid_responses_end_without_approval():
    graph, _, config, result = _start_approval()
    response = _response(result["__interrupt__"][0].value)
    bad = {**response, "decision": "maybe"}
    graph.invoke(Command(resume=bad), config)
    graph.invoke(Command(resume=bad), config)
    finished = graph.invoke(Command(resume=bad), config)
    assert finished["approval_decision"] is None
    assert finished["remediation_outcome"] == "no_action"
    assert finished["no_action_reason"] == "invalid_approval_response"
    assert finished["execution_receipt"] is None
    assert graph.get_state(config).next == ()


def test_second_resume_cannot_change_a_recorded_decision():
    graph, _, config, result = _start_approval()
    response = _response(result["__interrupt__"][0].value)
    first = graph.invoke(Command(resume=response), config)
    original = first["approval_decision"]
    graph.invoke(Command(resume={**response, "decision": "reject"}), config)
    checkpoint = graph.get_state(config).values
    assert checkpoint["approval_decision"] == original
    assert checkpoint["remediation_outcome"] == "approved"
    assert checkpoint["execution_receipt"] is None


def test_resume_on_another_thread_does_not_approve_pending_incident():
    graph, _, config, result = _start_approval()
    response = _response(result["__interrupt__"][0].value)
    wrong_config = {
        "configurable": {
            **config["configurable"],
            "thread_id": "another-thread",
        }
    }
    with pytest.raises(ValueError, match="resume the pending thread"):
        graph.invoke(Command(resume=response), wrong_config)
    pending = graph.get_state(config)
    assert pending.values["approval_decision"] is None
    assert pending.values["remediation_outcome"] == "awaiting_approval"
    assert pending.next == ("approval",)


def test_resume_cannot_switch_remediation_mode():
    graph, _, config, result = _start_approval()
    response = _response(result["__interrupt__"][0].value)
    changed_config = {
        "configurable": {
            **config["configurable"],
            "remediation_mode": "proposal",
        }
    }
    with pytest.raises(ValueError, match="requires approval mode"):
        graph.invoke(Command(resume=response), changed_config)
    assert graph.get_state(config).values["approval_decision"] is None


def test_ineligible_incident_never_interrupts_for_approval():
    graph, client, config, result = _start_approval(
        incident_id="diag-healthy", thread_id="review-healthy"
    )
    assert "__interrupt__" not in result
    assert result["action_proposal"] is None
    assert result["remediation_outcome"] == "no_action"
    assert result["approval_decision"] is None
    assert graph.get_state(config).next == ()
    assert client.calls == ["health", "metrics", "logs"]
