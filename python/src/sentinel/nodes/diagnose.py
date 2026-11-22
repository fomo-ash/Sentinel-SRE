"""Conservative deterministic diagnosis of simulated incidents."""

from sentinel.schemas import InvestigationState, validate_diagnosis


def _database_auth_evidence(state: InvestigationState) -> list[str]:
    """Recognize the explicit API login rejection in two diagnostic kinds."""
    incident = state["incident"]
    if incident["service"] != "api":
        return []
    incident_id = incident["id"]
    current = {
        item["id"]: item
        for item in state.get("evidence", [])
        if item["incident_id"] == incident_id and item["service"] == "api"
    }
    health_id = f"{incident_id}:health:database_auth"
    log_id = f"{incident_id}:log:db-auth-rejected"
    health = current.get(health_id)
    log = current.get(log_id)
    if not (
        health
        and health["kind"] == "health"
        and health["source_ref"]
        == f"GET /incidents/{incident_id}/health#checks/database_auth"
        and health["observation"]
        == (
            "unhealthy: API database login failed with "
            "SQLSTATE 28P01 (invalid_password)."
        )
        and log
        and log["kind"] == "log"
        and log["source_ref"]
        == f"GET /incidents/{incident_id}/logs#entries/db-auth-rejected"
        and log["observation"].endswith(
            "[error] database: Database driver could not open an API connection: "
            "SQLSTATE 28P01 (invalid_password)."
        )
    ):
        return []
    # A contradictory current observation needs human review, even when the
    # two positive observations are present.
    if any(
        (
            item["kind"] == "health"
            and item["observation"].startswith("healthy: API database login")
        )
        or (
            item["kind"] == "log"
            and "Database authentication succeeded" in item["observation"]
        )
        for item in current.values()
    ):
        return []
    return [health_id, log_id]


def diagnose_baseline(state: InvestigationState) -> dict:
    """Report one directly observed failure mechanism or abstain."""
    has_evidence = bool(state.get("evidence"))
    questions = [
        "Which component or dependency caused the observed behavior?"
        if has_evidence
        else "Which current diagnostic observations can be collected?"
    ]
    if state.get("diagnostic_failures"):
        questions.append("What would the failed diagnostic reads have shown?")
    historical_ids = list(
        dict.fromkeys(
            match["id"]
            for match in state.get("historical_matches", [])
            if isinstance(match, dict) and isinstance(match.get("id"), str)
        )
    )
    diagnosis = {
        "status": "inconclusive",
        "summary": "The available diagnostics do not establish a root cause.",
        "hypotheses": [],
        "unresolved_questions": questions,
        "historical_context_ids": historical_ids,
        "source": "deterministic",
    }
    supporting_ids = _database_auth_evidence(state)
    if supporting_ids:
        diagnosis.update(
            status="supported",
            summary="API database connections fail because database login is rejected.",
            hypotheses=[
                {
                    "cause": (
                        "Database authentication rejection prevents the API "
                        "from opening database connections."
                    ),
                    "supporting_evidence_ids": supporting_ids,
                }
            ],
            unresolved_questions=["Why is the database rejecting the API login?"],
        )
    return {
        "diagnosis": validate_diagnosis(diagnosis, state),
        "diagnosis_source": "deterministic",
        "diagnosis_failure_reason": None,
    }
