"""Convert validated current observations to checkpointed evidence."""

from sentinel.schemas import Evidence, InvestigationState


def _as_evidence(state: InvestigationState) -> list[Evidence]:
    result = state.get("diagnostic_result")
    check = state.get("pending_check")
    if result is None or check is None:
        return []
    kind = check["kind"]
    incident_id = result["incident_id"]
    service = result["service"]
    observed_at = result["observed_at"]
    items_key = {"health": "checks", "metrics": "metrics", "logs": "entries"}[kind]
    evidence_kind = {"health": "health", "metrics": "metric", "logs": "log"}[kind]
    evidence = []
    for item in result[items_key]:
        if kind == "health":
            observation = f"{item['status']}: {item['detail']}"
        elif kind == "metrics":
            observation = (
                f"{item['name']}={item['value']:g} {item['unit']} over {item['window']}"
            )
        else:
            observation = (
                f"{item['timestamp']} [{item['level']}] "
                f"{item['component']}: {item['message']}"
            )
        evidence.append(
            {
                "id": f"{incident_id}:{evidence_kind}:{item['id']}",
                "incident_id": incident_id,
                "service": service,
                "kind": evidence_kind,
                "source_ref": (
                    f"GET /incidents/{incident_id}/{kind}#{items_key}/{item['id']}"
                ),
                "observed_at": observed_at,
                "observation": observation[:320],
            }
        )
    return evidence


def evaluate_evidence(state: InvestigationState) -> dict:
    """Keep tool failures separate; a failed read contributes no evidence."""
    check = state["pending_check"]
    if check is None:
        raise ValueError("No diagnostic check was evaluated")
    current = list(state.get("evidence", []))
    seen = {item["id"] for item in current}
    for item in _as_evidence(state):
        if item["id"] not in seen:
            current.append(item)
            seen.add(item["id"])
    return {
        "evidence": current,
        "completed_checks": [*state.get("completed_checks", []), check["kind"]],
        "diagnostic_result": None,
        "investigation_stop_reason": (
            "incident_not_found"
            if state.get("diagnostic_error") == "not_found"
            else None
        ),
    }
