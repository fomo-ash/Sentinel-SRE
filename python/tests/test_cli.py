"""Interactive operator input never grants a synthetic action by default."""

import argparse
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel import cli
from sentinel.tools.remediation import RemediationClientError

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "services/mock-service/internal/incidents/fixtures"
)


class FixtureDiagnostics:
    def __init__(self):
        self.action = None
        self.health_reads = 0

    def read(self, kind, incident_id, service, *, limit=None):
        if kind == "health":
            self.health_reads += 1
            if self.health_reads > 1 and self.action.receipt is not None:
                receipt = self.action.receipt
                return {
                    "incident_id": incident_id,
                    "service": service,
                    "observed_at": receipt["applied_at"],
                    "status": "healthy",
                    "checks": [
                        {
                            "id": "db_login_restored",
                            "status": "healthy",
                            "detail": "Simulated database login restored.",
                        }
                    ],
                }
        snapshots = json.loads((_FIXTURE_DIR / f"{kind}_v1.json").read_text())
        snapshot = next(
            item for item in snapshots if item["incident_id"] == incident_id
        )
        assert snapshot["service"] == service
        return snapshot


class FakeAction:
    def __init__(self, error=None):
        self.error = error
        self.receipt = None
        self.calls = []

    def get_receipt(self, action_id):
        self.calls.append("get")
        return self.receipt

    def execute(self, proposal, approval, action_id):
        self.calls.append("post")
        if self.error is not None:
            raise self.error
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


def _clients():
    diagnostics = FixtureDiagnostics()
    action = FakeAction()
    diagnostics.action = action
    return diagnostics, action


def _args(incident_id="diag-db-auth"):
    return argparse.Namespace(
        id=incident_id,
        service="api",
        summary="Investigate the synthetic incident.",
    )


@pytest.mark.parametrize(
    "decision,expected_outcome,expected_calls,health_reads",
    [
        ("approve", "verified", ["get", "post"], 2),
        ("reject", "rejected", [], 1),
    ],
)
def test_operator_sees_checkpoint_before_explicit_decision(
    decision, expected_outcome, expected_calls, health_reads
):
    diagnostics, action = _clients()
    output = []
    answers = iter([decision, "local-reviewer"])

    def read_input(_):
        # The proposal and pending checkpoint must be visible before any input.
        shown = json.loads(output[0])
        assert shown["checkpoint_outcome"] == "awaiting_approval"
        assert shown["approval_recorded"] is False
        assert shown["action_receipt_recorded"] is False
        assert shown["proposal"]["action"] == "restore_simulated_db_login"
        assert (
            shown["current_evidence_ids"] == shown["proposal"]["current_evidence_ids"]
        )
        assert action.calls == []
        return next(answers)

    code = cli.run_operator_flow(
        _args(), diagnostics, action, read_input=read_input, emit=output.append
    )
    assert code == 0
    report = json.loads(output[-1])["report"]
    assert report["outcome"] == expected_outcome
    assert action.calls == expected_calls
    assert diagnostics.health_reads == health_reads
    if decision == "approve":
        assert report["approval_decision"]["decision"] == "approve"
        assert report["execution_receipt"]["status"] == "applied"
        assert report["verification_result"]["status"] == "verified"
    else:
        assert report["execution_receipt"] is None


def test_ineligible_incident_reports_without_prompt_or_action():
    diagnostics, action = _clients()
    output = []

    def no_prompt(_):
        raise AssertionError("An ineligible incident must not request approval")

    code = cli.run_operator_flow(
        _args("diag-healthy"),
        diagnostics,
        action,
        read_input=no_prompt,
        emit=output.append,
    )
    assert code == 0
    assert json.loads(output[-1])["report"]["outcome"] == "no_action"
    assert action.calls == []


@pytest.mark.parametrize(
    "answers", [["", "maybe", "yes"], ["approve", "  ", "x", "reject", ""]]
)
def test_invalid_input_ends_without_recorded_decision_or_action(answers):
    diagnostics, action = _clients()
    output = []
    inputs = iter(answers)
    code = cli.run_operator_flow(
        _args(),
        diagnostics,
        action,
        read_input=lambda _: next(inputs),
        emit=output.append,
    )
    assert code == 2
    assert "No action was requested" in output[-1]
    assert action.calls == []


def test_closed_input_ends_without_action():
    diagnostics, action = _clients()
    output = []

    def closed(_):
        raise EOFError

    assert (
        cli.run_operator_flow(
            _args(), diagnostics, action, read_input=closed, emit=output.append
        )
        == 2
    )
    assert action.calls == []


def test_execution_unknown_is_reported_without_health_claim():
    diagnostics, action = _clients()
    action.error = RemediationClientError("execution_unknown")
    output = []
    answers = iter(["approve", "local-reviewer"])
    code = cli.run_operator_flow(
        _args(),
        diagnostics,
        action,
        read_input=lambda _: next(answers),
        emit=output.append,
    )
    assert code == 0
    report = json.loads(output[-1])["report"]
    assert report["outcome"] == "execution_unknown"
    assert report["verification_status"] is None
    assert diagnostics.health_reads == 1


def test_cli_requires_tty_before_constructing_action_client(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    def unexpected():
        raise AssertionError("No HTTP client should be constructed")

    monkeypatch.setattr(cli, "HTTPDiagnosticClient", unexpected)
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "--id",
                "diag-db-auth",
                "--service",
                "api",
                "--summary",
                "Synthetic review",
                "--simulated-remediation",
            ]
        )
    assert error.value.code == 2
    assert "requires an interactive terminal" in capsys.readouterr().err


def test_cli_flag_wires_interactive_clients_and_keeps_read_only_default(
    monkeypatch, capsys
):
    diagnostics, action = _clients()
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli, "HTTPDiagnosticClient", lambda: nullcontext(diagnostics))
    monkeypatch.setattr(
        cli, "HTTPRemediationClient", lambda incident_id: nullcontext(action)
    )
    answers = iter(["reject", "local-reviewer"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    arguments = ["--id", "diag-db-auth", "--service", "api", "--summary", "test"]
    assert cli.main([*arguments, "--simulated-remediation"]) == 0
    assert action.calls == []
    assert '"outcome": "rejected"' in capsys.readouterr().out

    diagnostics, action = _clients()
    monkeypatch.setattr(cli, "HTTPDiagnosticClient", lambda: nullcontext(diagnostics))
    assert cli.main(arguments) == 0
    assert action.calls == []
    assert '"investigation_stop_reason": "checks_complete"' in capsys.readouterr().out
