"""Safety behavior of the standalone deterministic diagnosis baseline."""

from copy import deepcopy

from sentinel.nodes.diagnose import diagnose_baseline


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
