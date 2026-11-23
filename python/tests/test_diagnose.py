"""Safety behavior of the standalone deterministic diagnosis baseline."""

import json
from copy import deepcopy

import pytest

from sentinel.nodes.diagnose import (
    TemporaryProviderError,
    build_llm_diagnose_node,
    diagnose_baseline,
)


def _state():
    return {
        "incident": {
            "id": "diag-degraded",
            "service": "api",
            "summary": "API requests are slow for several users",
        },
        "final_severity": "unknown",
        "evidence": [
            {
                "id": "diag-degraded:health:readiness",
                "incident_id": "diag-degraded",
                "service": "api",
                "kind": "health",
                "source_ref": "GET /incidents/diag-degraded/health#checks/readiness",
                "observed_at": "2026-01-01T00:05:00Z",
                "observation": "degraded: readiness probe was slow",
            },
            {
                "id": "diag-degraded:metric:http_error_ratio",
                "incident_id": "diag-degraded",
                "service": "api",
                "kind": "metric",
                "source_ref": (
                    "GET /incidents/diag-degraded/metrics#metrics/http_error_ratio"
                ),
                "observed_at": "2026-01-01T00:05:00Z",
                "observation": "http_error_ratio=0.12 ratio over 5m",
            },
            {
                "id": "diag-degraded:log:timeout",
                "incident_id": "diag-degraded",
                "service": "api",
                "kind": "log",
                "source_ref": "GET /incidents/diag-degraded/logs#entries/timeout",
                "observed_at": "2026-01-01T00:05:00Z",
                "observation": "A request timed out",
            },
        ],
        "diagnostic_failures": [],
        "historical_matches": [],
    }


def _supported_state():
    state = _state()
    state["incident"]["id"] = "diag-db-auth"
    state["evidence"] = [
        {
            "id": "diag-db-auth:health:database_auth",
            "incident_id": "diag-db-auth",
            "service": "api",
            "kind": "health",
            "source_ref": "GET /incidents/diag-db-auth/health#checks/database_auth",
            "observed_at": "2026-01-01T00:20:00Z",
            "observation": (
                "unhealthy: API database login failed with "
                "SQLSTATE 28P01 (invalid_password)."
            ),
        },
        {
            "id": "diag-db-auth:log:db-auth-rejected",
            "incident_id": "diag-db-auth",
            "service": "api",
            "kind": "log",
            "source_ref": "GET /incidents/diag-db-auth/logs#entries/db-auth-rejected",
            "observed_at": "2026-01-01T00:20:00Z",
            "observation": (
                "2026-01-01T00:19:42Z [error] database: Database driver could "
                "not open an API connection: SQLSTATE 28P01 (invalid_password)."
            ),
        },
    ]
    return state


def test_explicit_database_auth_failure_is_supported_by_health_and_log():
    state = _supported_state()
    before = deepcopy(state)
    update = diagnose_baseline(state)
    diagnosis = update["diagnosis"]
    assert diagnosis["status"] == "supported"
    assert diagnosis["hypotheses"][0]["supporting_evidence_ids"] == [
        "diag-db-auth:health:database_auth",
        "diag-db-auth:log:db-auth-rejected",
    ]
    assert diagnosis["unresolved_questions"] == [
        "Why is the database rejecting the API login?"
    ]
    assert "final_severity" not in update
    assert state == before


def test_database_auth_rule_abstains_on_missing_conflicting_or_foreign_evidence():
    state = _supported_state()
    state["evidence"] = state["evidence"][:1]
    assert diagnose_baseline(state)["diagnosis"]["status"] == "inconclusive"

    state = _supported_state()
    state["evidence"][1]["observation"] = (
        "2026-01-01T00:19:42Z [error] database: Connection timed out."
    )
    assert diagnose_baseline(state)["diagnosis"]["status"] == "inconclusive"

    state = _supported_state()
    state["evidence"][1]["incident_id"] = "diag-other"
    assert diagnose_baseline(state)["diagnosis"]["status"] == "inconclusive"

    state = _supported_state()
    state["evidence"].append(
        {
            **state["evidence"][0],
            "id": "diag-db-auth:health:database_auth_recovered",
            "observation": "healthy: API database login succeeded.",
        }
    )
    assert diagnose_baseline(state)["diagnosis"]["status"] == "inconclusive"


def test_correlated_symptoms_do_not_become_an_invented_cause():
    state = _state()
    before = deepcopy(state)
    update = diagnose_baseline(state)
    assert update["diagnosis"]["status"] == "inconclusive"
    assert update["diagnosis"]["hypotheses"] == []
    assert update["diagnosis"]["source"] == "deterministic"
    assert update["diagnosis_source"] == "deterministic"
    assert update["diagnosis_failure_reason"] is None
    assert "final_severity" not in update
    assert state == before


def test_no_current_observations_produce_a_missing_data_question():
    state = _state()
    state["evidence"] = []
    update = diagnose_baseline(state)
    assert update["diagnosis"]["status"] == "inconclusive"
    assert (
        "Which current diagnostic observations"
        in update["diagnosis"]["unresolved_questions"][0]
    )


def test_failed_diagnostic_is_not_evidence_of_a_cause():
    state = _state()
    state["evidence"] = []
    state["diagnostic_failures"] = [
        {"kind": "health", "category": "timeout", "attempts": 1, "latency_ms": 2.0}
    ]
    update = diagnose_baseline(state)
    assert update["diagnosis"]["hypotheses"] == []
    assert update["diagnosis"]["status"] == "inconclusive"
    assert any(
        "failed diagnostic reads" in question
        for question in update["diagnosis"]["unresolved_questions"]
    )


def test_history_is_context_only_and_duplicate_ids_are_removed():
    state = _state()
    state["historical_matches"] = [{"id": "hist-one"}, {"id": "hist-one"}]
    update = diagnose_baseline(state)
    assert update["diagnosis"]["historical_context_ids"] == ["hist-one"]
    assert update["diagnosis"]["hypotheses"] == []
    assert update["diagnosis"]["status"] == "inconclusive"


class FakeDiagnosisModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _model_result(*, supported=False):
    return {
        "status": "supported" if supported else "inconclusive",
        "summary": "Database login rejection observed."
        if supported
        else "Cause unknown.",
        "hypotheses": (
            [
                {
                    "cause": "Database login rejection prevents API connections.",
                    "supporting_evidence_ids": [
                        "diag-db-auth:health:database_auth",
                        "diag-db-auth:log:db-auth-rejected",
                    ],
                }
            ]
            if supported
            else []
        ),
        "unresolved_questions": ["Why was login rejected?"],
        "historical_context_ids": [],
    }


def test_model_diagnosis_uses_only_current_input_and_preserves_policy_severity():
    state = _state()
    state["historical_matches"] = [
        {
            "id": "hist-one",
            "score": 0.8,
            "historical_incident": {
                "summary": "Past errors",
                "confirmed_cause": "Past queue failure",
                "resolution": "Past queue repair",
                "provenance": {"type": "synthetic"},
            },
        }
    ]
    before = deepcopy(state)
    model = FakeDiagnosisModel([_model_result()])
    update = build_llm_diagnose_node(model)(state)
    payload = json.loads(model.requests[0])
    assert set(payload) == {
        "incident",
        "current_evidence",
        "diagnostic_failures",
        "investigation_stop_reason",
        "historical_context",
    }
    assert payload["current_evidence"] == state["evidence"]
    assert payload["historical_context"][0]["confirmed_cause"] == "Past queue failure"
    assert update["diagnosis"]["status"] == "inconclusive"
    assert update["diagnosis_source"] == "llm"
    assert update["diagnosis_model_calls"] == 1
    assert update["diagnosis_repair_attempted"] is False
    assert "final_severity" not in update
    assert state == before


def test_supported_model_result_is_restricted_to_policy_confirmed_mechanism():
    state = _supported_state()
    candidate = _model_result(supported=True)
    candidate["hypotheses"][0]["cause"] = (
        "Credential rotation caused database login rejection."
    )
    update = build_llm_diagnose_node(FakeDiagnosisModel([candidate]))(state)
    assert update["diagnosis"]["status"] == "supported"
    assert update["diagnosis"]["source"] == "llm"
    assert update["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] == [
        "diag-db-auth:health:database_auth",
        "diag-db-auth:log:db-auth-rejected",
    ]
    assert "rotation" not in update["diagnosis"]["hypotheses"][0]["cause"]
    assert "Why was login rejected?" in update["diagnosis"]["unresolved_questions"]


def test_invalid_output_gets_one_grounded_repair():
    model = FakeDiagnosisModel([{"status": "supported"}, _model_result()])
    update = build_llm_diagnose_node(model)(_state())
    assert update["diagnosis_source"] == "llm"
    assert update["diagnosis_model_calls"] == 2
    assert update["diagnosis_repair_attempted"] is True
    assert "Original input:" in model.requests[1]
    assert "current_evidence" in model.requests[1]


@pytest.mark.parametrize("invalid", ["not json", {"status": "supported"}])
def test_exhausted_repair_returns_inconclusive_even_with_supported_baseline(invalid):
    state = _supported_state()
    model = FakeDiagnosisModel([invalid, invalid])
    update = build_llm_diagnose_node(model)(state)
    assert update["diagnosis"]["status"] == "inconclusive"
    assert update["diagnosis"]["hypotheses"] == []
    assert update["diagnosis_source"] == "fallback"
    assert update["diagnosis_failure_reason"] == "invalid_output"
    assert update["diagnosis_model_calls"] == 2


def test_history_as_citation_is_rejected_and_never_becomes_current_evidence():
    state = _supported_state()
    state["historical_matches"] = [{"id": "hist-one"}]
    invalid = _model_result(supported=True)
    invalid["hypotheses"][0]["supporting_evidence_ids"] = ["hist-one"]
    update = build_llm_diagnose_node(FakeDiagnosisModel([invalid, invalid]))(state)
    assert update["diagnosis_source"] == "fallback"
    assert update["diagnosis"]["status"] == "inconclusive"


def test_transient_failure_retries_then_recovers():
    model = FakeDiagnosisModel(
        [TemporaryProviderError("rate_limited"), _model_result()]
    )
    update = build_llm_diagnose_node(model, sleep=lambda delay: None)(_state())
    assert update["diagnosis_source"] == "llm"
    assert update["diagnosis_model_calls"] == 2


@pytest.mark.parametrize(
    ("failure", "reason", "calls"),
    [
        (TimeoutError(), "timeout", 3),
        (ConnectionError(), "provider_unavailable", 3),
        (TemporaryProviderError("rate_limited"), "rate_limited", 3),
        (RuntimeError(), "provider_error", 1),
    ],
)
def test_model_failures_fall_back_with_fixed_reason(failure, reason, calls):
    model = FakeDiagnosisModel([failure] * calls)
    update = build_llm_diagnose_node(model, sleep=lambda delay: None)(_state())
    assert update["diagnosis"]["status"] == "inconclusive"
    assert update["diagnosis_source"] == "fallback"
    assert update["diagnosis_failure_reason"] == reason
    assert update["diagnosis_model_calls"] == calls


def test_missing_corroboration_rejects_supported_model_claim():
    state = _supported_state()
    state["evidence"] = state["evidence"][:1]
    result = _model_result(supported=True)
    update = build_llm_diagnose_node(FakeDiagnosisModel([result, result]))(state)
    assert update["diagnosis_source"] == "fallback"
    assert update["diagnosis"]["status"] == "inconclusive"


def test_contradictory_current_observation_rejects_supported_model_claim():
    state = _supported_state()
    state["evidence"].append(
        {
            **state["evidence"][0],
            "id": "diag-db-auth:health:login_recovered",
            "observation": "healthy: API database login succeeded.",
        }
    )
    candidate = _model_result(supported=True)
    update = build_llm_diagnose_node(FakeDiagnosisModel([candidate, candidate]))(state)
    assert update["diagnosis_source"] == "fallback"
    assert update["diagnosis"]["status"] == "inconclusive"
