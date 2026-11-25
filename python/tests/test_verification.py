"""Fresh incident health verification and truthful terminal reports."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from langgraph.types import Command

from sentinel.graph import build_graph
from sentinel.tools.diagnostics import DiagnosticReadError
from sentinel.tools.remediation import RemediationClientError

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "services/mock-service/internal/incidents/fixtures"
)


class PostHealthClient:
    def __init__(self, post_builder=None):
        self.post_builder = post_builder
        self.action_client = None
        self.health_reads = 0

    def read(self, kind, incident_id, service, *, limit=None):
        if kind == "health":
            self.health_reads += 1
            if self.health_reads > 1:
                return self.post_builder(self.action_client.receipt)
        snapshots = json.loads((_FIXTURE_DIR / f"{kind}_v1.json").read_text())
        snapshot = next(
            item for item in snapshots if item["incident_id"] == incident_id
        )
        assert snapshot["service"] == service
        return snapshot


class ActionClient:
    def __init__(self, execute_error=None):
        self.execute_error = execute_error
        self.receipt = None
        self.calls = []

    def get_receipt(self, action_id):
        self.calls.append("get")
        return self.receipt

    def execute(self, proposal, approval, action_id):
        self.calls.append("post")
        if self.execute_error is not None:
            raise self.execute_error
        self.receipt = {
            "action_id": action_id,
            "incident_id": proposal["incident_id"],
            "service": proposal["service"],
            "action": proposal["action"],
            "proposal_fingerprint": proposal["fingerprint"],
            "approval_reference": approval["decision_id"],
            "status": "applied",
            "applied_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        }
        return self.receipt


def _restored(receipt):
    return {
        "incident_id": receipt["incident_id"],
        "service": receipt["service"],
        "observed_at": receipt["applied_at"],
        "status": "healthy",
        "checks": [
            {
                "id": "db_login_restored",
                "status": "healthy",
                "detail": "The simulated database login check passed.",
            }
        ],
    }


def _start(
    post_builder=_restored,
    *,
    mode="simulated",
    incident_id="diag-db-auth",
    execute_error=None,
):
    diagnostics = PostHealthClient(post_builder)
    action = ActionClient(execute_error)
    diagnostics.action_client = action
    graph = build_graph(diagnostic_client=diagnostics, remediation_client=action)
    config = {
        "configurable": {
            "thread_id": "task-seven-review",
            "investigation_mode": "simulated",
            "remediation_mode": mode,
        }
    }
    pending = graph.invoke(
        {
            "incident": {
                "id": incident_id,
                "service": "api",
                "summary": "Investigate this synthetic incident.",
            }
        },
        config,
    )
    return graph, diagnostics, action, config, pending


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


def test_fresh_restored_health_verifies_effect_and_reports_it():
    graph, diagnostics, action, config, pending = _start()
    pre_action_evidence = list(graph.get_state(config).values["evidence"])
    pre_severity = graph.get_state(config).values["final_severity"]
    assert diagnostics.health_reads == 1
    assert action.calls == []
    result = graph.invoke(Command(resume=_response(pending)), config)
    receipt = result["execution_receipt"]
    verification = result["verification_result"]
    report = result["remediation_report"]
    assert result["remediation_outcome"] == "verified"
    assert verification == {
        "action_id": receipt["action_id"],
        "incident_id": "diag-db-auth",
        "service": "api",
        "status": "verified",
        "observation_id": "diag-db-auth:health:db_login_restored",
        "observed_at": receipt["applied_at"],
        "failure_reason": None,
    }
    assert report["outcome"] == "verified"
    assert report["action_id"] == receipt["action_id"]
    assert report["action_proposal"] == result["action_proposal"]
    assert report["approval_decision"] == result["approval_decision"]
    assert report["execution_receipt"] == receipt
    assert report["verification_result"] == verification
    assert report["verification_status"] == "verified"
    assert report["final_severity"] == pre_severity
    assert report["diagnosis_status"] == "supported"
    assert result["evidence"] == pre_action_evidence
    assert diagnostics.health_reads == 2
    assert action.calls == ["get", "post"]
    assert graph.get_state(config).next == ()


def test_stale_health_cannot_verify_an_applied_action():
    def stale(receipt):
        snapshot = _restored(receipt)
        snapshot["observed_at"] = (
            datetime.fromisoformat(receipt["applied_at"]) - timedelta(seconds=1)
        ).isoformat()
        return snapshot

    graph, diagnostics, _, config, pending = _start(stale)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "verification_failed"
    assert result["verification_result"]["failure_reason"] == "stale"
    assert result["remediation_report"]["outcome"] == "verification_failed"
    assert result["remediation_report"]["verification_status"] == "failed"
    assert diagnostics.health_reads == 2


@pytest.mark.parametrize("kind", ["overall", "check"])
def test_unhealthy_post_action_health_is_verification_failure(kind):
    def unhealthy(receipt):
        snapshot = _restored(receipt)
        if kind == "overall":
            snapshot["status"] = "unhealthy"
        else:
            snapshot["checks"][0]["status"] = "unhealthy"
        return snapshot

    graph, _, _, config, pending = _start(unhealthy)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "verification_failed"
    assert result["verification_result"]["failure_reason"] == "unhealthy"
    assert result["remediation_report"]["action_id"] is not None
    assert result["remediation_report"]["outcome"] == "verification_failed"


def test_missing_restored_check_keeps_recovery_unknown():
    def missing(receipt):
        snapshot = _restored(receipt)
        snapshot["checks"][0]["id"] = "another_check"
        return snapshot

    graph, _, _, config, pending = _start(missing)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "verification_unknown"
    assert result["verification_result"]["failure_reason"] == "missing_observation"
    assert result["verification_result"]["observation_id"] is None
    assert result["remediation_report"]["verification_status"] == "unknown"


@pytest.mark.parametrize(
    "post_builder,reason",
    [
        (
            lambda receipt: {**_restored(receipt), "incident_id": "diag-other"},
            "invalid_response",
        ),
        (lambda receipt: {**_restored(receipt), "checks": "wrong"}, "invalid_response"),
        (
            lambda receipt: (_ for _ in ()).throw(DiagnosticReadError("timeout")),
            "timeout",
        ),
        (
            lambda receipt: (_ for _ in ()).throw(DiagnosticReadError("unavailable")),
            "unavailable",
        ),
        (
            lambda receipt: (_ for _ in ()).throw(DiagnosticReadError("not_found")),
            "not_found",
        ),
    ],
)
def test_bad_or_unavailable_health_keeps_recovery_unknown(post_builder, reason):
    graph, diagnostics, _, config, pending = _start(post_builder)
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "verification_unknown"
    assert result["verification_result"]["failure_reason"] == reason
    assert result["remediation_report"]["outcome"] == "verification_unknown"
    assert diagnostics.health_reads == 2


def test_rejection_and_no_proposal_get_reports_without_action():
    graph, diagnostics, action, config, pending = _start()
    rejected = graph.invoke(Command(resume=_response(pending, "reject")), config)
    assert rejected["remediation_outcome"] == "rejected"
    assert rejected["remediation_report"]["outcome"] == "rejected"
    assert rejected["remediation_report"]["action_id"] is None
    assert rejected["verification_result"] is None
    assert diagnostics.health_reads == 1
    assert action.calls == []

    _, diagnostics, action, _, no_proposal = _start(incident_id="diag-healthy")
    assert "__interrupt__" not in no_proposal
    assert no_proposal["remediation_outcome"] == "no_action"
    assert no_proposal["remediation_report"]["outcome"] == "no_action"
    assert diagnostics.health_reads == 1
    assert action.calls == []


def test_execution_unknown_reports_no_verified_claim_or_health_read():
    graph, diagnostics, action, config, pending = _start(
        execute_error=RemediationClientError("execution_unknown")
    )
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "execution_unknown"
    assert result["remediation_report"]["outcome"] == "execution_unknown"
    assert result["remediation_report"]["verification_status"] is None
    assert result["verification_result"] is None
    assert diagnostics.health_reads == 1
    assert action.calls == ["get", "post"]


def test_approval_only_mode_reports_approval_without_action_or_verification():
    graph, diagnostics, action, config, pending = _start(mode="approval")
    result = graph.invoke(Command(resume=_response(pending)), config)
    assert result["remediation_outcome"] == "approved"
    assert result["remediation_report"]["outcome"] == "approved"
    assert result["remediation_report"]["action_id"] is None
    assert result["verification_result"] is None
    assert diagnostics.health_reads == 1
    assert action.calls == []
