"""Pure proposal policy and opt-in graph route for Phase 6 Task 2."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sentinel.graph import build_graph
from sentinel.nodes.propose_remediation import propose_remediation
from sentinel.tools.diagnostics import DiagnosticReadError

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "services/mock-service/internal/incidents/fixtures"
)


class FixtureClient:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    def read(self, kind, incident_id, service, *, limit=None):
        self.calls.append(kind)
        if kind in self.failures:
            raise self.failures[kind]
        snapshots = json.loads((_FIXTURE_DIR / f"{kind}_v1.json").read_text())
        snapshot = next(
            item for item in snapshots if item["incident_id"] == incident_id
        )
        assert snapshot["service"] == service
        return deepcopy(snapshot)


def _run(incident_id="diag-db-auth", service="api", *, client=None, graph=None):
    client = client or FixtureClient()
    graph = graph or build_graph(diagnostic_client=client)
    config = {
        "configurable": {
            "thread_id": f"proposal-{incident_id}",
            "investigation_mode": "simulated",
            "remediation_mode": "proposal",
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
    return result, graph, client, config


def test_supported_fixture_produces_one_checkpointed_proposal_without_action():
    result, graph, client, config = _run()
    proposal = result["action_proposal"]
    assert proposal["incident_id"] == "diag-db-auth"
    assert proposal["service"] == "api"
    assert proposal["action"] == "restore_simulated_db_login"
    assert proposal["current_evidence_ids"] == [
        "diag-db-auth:health:database_auth",
        "diag-db-auth:log:db-auth-rejected",
    ]
    assert len(proposal["fingerprint"]) == 64
    assert proposal["proposal_id"].startswith("proposal-")
    assert result["no_action_reason"] is None
    assert result["remediation_outcome"] == "proposal_ready"
    assert result["remediation_report"]["outcome"] == "proposal_ready"
    assert result["remediation_report"]["action_id"] is None
    assert result["approval_decision"] is None
    assert result["execution_receipt"] is None
    assert result["final_severity"] == "unknown"
    assert client.calls == ["health", "metrics", "logs"]
    assert graph.get_state(config).values == result


@pytest.mark.parametrize(
    ("incident_id", "service"),
    [
        ("diag-healthy", "api"),
        ("diag-missing", "worker"),
        ("diag-db-auth-alt", "api"),
        ("diag-degraded", "api"),
    ],
)
def test_other_fixtures_have_no_action(incident_id, service):
    result, _, client, _ = _run(incident_id, service)
    assert result["action_proposal"] is None
    assert result["no_action_reason"] == "ineligible_incident"
    assert result["remediation_outcome"] == "no_action"
    assert result["remediation_report"]["outcome"] == "no_action"
    assert result["approval_decision"] is None
    assert result["execution_receipt"] is None
    assert client.calls == ["health", "metrics", "logs"]


def test_incomplete_diagnostics_and_inconclusive_diagnosis_abstain():
    failed_client = FixtureClient({"logs": DiagnosticReadError("timeout")})
    result, _, _, _ = _run(client=failed_client)
    assert result["diagnostic_failures"]
    assert result["action_proposal"] is None
    assert result["no_action_reason"] == "incomplete_diagnostics"

    good, _, _, _ = _run()
    good["diagnosis"] = {
        **good["diagnosis"],
        "status": "inconclusive",
        "hypotheses": [],
    }
    update = propose_remediation(good)
    assert update["action_proposal"] is None
    assert update["no_action_reason"] == "unsupported_diagnosis"


def test_conflicting_and_historical_only_evidence_cannot_propose():
    state, _, _, _ = _run()
    state["evidence"].append(
        {
            "id": "diag-db-auth:health:later",
            "incident_id": "diag-db-auth",
            "service": "api",
            "kind": "health",
            "source_ref": "GET /incidents/diag-db-auth/health#checks/later",
            "observed_at": "2026-01-01T00:21:00Z",
            "observation": "healthy: API database login succeeded.",
        }
    )
    assert propose_remediation(state)["no_action_reason"] == "unsupported_evidence"

    state, _, _, _ = _run()
    state["evidence"] = []
    state["historical_matches"] = [{"id": "hist-db-auth"}]
    assert propose_remediation(state)["action_proposal"] is None


def test_supported_shaped_diagnosis_with_wrong_mechanism_cannot_propose():
    state, _, _, _ = _run()
    state["diagnosis"]["hypotheses"][0]["cause"] = "A network outage caused errors."
    update = propose_remediation(state)
    assert update["action_proposal"] is None
    assert update["no_action_reason"] == "unsupported_evidence"
    assert update["remediation_outcome"] == "no_action"


def test_proposal_fingerprint_is_stable_and_binds_observation_time():
    state, _, _, _ = _run()
    original = state["action_proposal"]
    assert propose_remediation(state)["action_proposal"] == original
    state["evidence"][0]["observed_at"] = "2026-01-01T00:20:01Z"
    changed = propose_remediation(state)["action_proposal"]
    assert changed["current_evidence_ids"] == original["current_evidence_ids"]
    assert changed["fingerprint"] != original["fingerprint"]
    assert changed["proposal_id"] != original["proposal_id"]


def test_model_diagnosis_can_reach_same_deterministic_proposal_policy():
    class FakeModel:
        def invoke(self, payload):
            return {
                "status": "supported",
                "summary": "Database login is rejected.",
                "hypotheses": [
                    {
                        "cause": "The database rejected API login.",
                        "supporting_evidence_ids": [
                            "diag-db-auth:health:database_auth",
                            "diag-db-auth:log:db-auth-rejected",
                        ],
                    }
                ],
                "unresolved_questions": ["Why was login rejected?"],
                "historical_context_ids": [],
            }

    client = FixtureClient()
    graph = build_graph(diagnostic_client=client, diagnosis_model=FakeModel())
    result = graph.invoke(
        {
            "incident": {
                "id": "diag-db-auth",
                "service": "api",
                "summary": "Investigate the synthetic API incident.",
            }
        },
        {
            "configurable": {
                "thread_id": "proposal-model",
                "investigation_mode": "simulated",
                "diagnosis_mode": "llm",
                "remediation_mode": "proposal",
            }
        },
    )
    assert result["diagnosis_source"] == "llm"
    assert result["action_proposal"]["action"] == "restore_simulated_db_login"
    assert result["approval_decision"] is None
    assert result["execution_receipt"] is None
    assert client.calls == ["health", "metrics", "logs"]


def test_proposal_mode_requires_simulated_investigation_and_valid_mode():
    client = FixtureClient()
    graph = build_graph(diagnostic_client=client)
    incident = {
        "incident": {
            "id": "diag-db-auth",
            "service": "api",
            "summary": "Investigate.",
        }
    }
    with pytest.raises(ValueError, match="requires simulated investigation"):
        graph.invoke(
            incident,
            {"configurable": {"thread_id": "off", "remediation_mode": "proposal"}},
        )
    with pytest.raises(ValueError, match="Unsupported remediation mode"):
        graph.invoke(
            incident,
            {
                "configurable": {
                    "thread_id": "bad-mode",
                    "investigation_mode": "simulated",
                    "remediation_mode": "execute",
                }
            },
        )
    assert client.calls == []


def test_default_route_and_reused_thread_clear_prior_proposal():
    client = FixtureClient()
    graph = build_graph(diagnostic_client=client)
    first, _, _, config = _run(client=client, graph=graph)
    assert first["action_proposal"] is not None
    second = graph.invoke(
        {
            "incident": {
                "id": "diag-db-auth",
                "service": "api",
                "summary": "Investigate again.",
            }
        },
        {
            "configurable": {
                "thread_id": config["configurable"]["thread_id"],
                "investigation_mode": "simulated",
            }
        },
    )
    assert second["action_proposal"] is None
    assert second["no_action_reason"] is None
    assert second["remediation_outcome"] is None
    assert client.calls == ["health", "metrics", "logs"] * 2
