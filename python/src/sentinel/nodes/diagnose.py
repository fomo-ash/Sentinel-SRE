"""Conservative deterministic and optional model diagnosis."""

import json
import math
import time
from collections.abc import Callable
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from sentinel.schemas import (
    DiagnosisFailure,
    InvestigationState,
    ModelDiagnosis,
    validate_diagnosis,
)

_model_diagnosis_adapter = TypeAdapter(ModelDiagnosis)


class DiagnosisModel(Protocol):
    """Provider-neutral interface for a structured diagnosis model."""

    def invoke(self, payload: str) -> object: ...


class TemporaryProviderError(Exception):
    """Fixed retry reason supplied by an optional provider adapter."""

    def __init__(self, reason: DiagnosisFailure):
        self.reason = reason
        super().__init__(reason)


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


def _model_input(state: InvestigationState) -> str:
    """Send current observations and labeled past context, never eval labels."""
    incident = state["incident"]
    current = [
        item
        for item in state.get("evidence", [])
        if item["incident_id"] == incident["id"]
        and item["service"] == incident["service"]
    ]
    history = []
    for match in state.get("historical_matches", []):
        past = match.get("historical_incident", {})
        history.append(
            {
                "id": match["id"],
                "score": match.get("score"),
                "summary": past.get("summary"),
                "confirmed_cause": past.get("confirmed_cause"),
                "resolution": past.get("resolution"),
                "provenance": past.get("provenance"),
            }
        )
    return json.dumps(
        {
            "incident": incident,
            "current_evidence": current,
            "diagnostic_failures": state.get("diagnostic_failures", []),
            "investigation_stop_reason": state.get("investigation_stop_reason"),
            "historical_context": history,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _validated_model_diagnosis(raw: object, state: InvestigationState) -> dict:
    """Accept only a narrow, policy-confirmed supported mechanism."""
    result = _model_diagnosis_adapter.validate_python(raw, strict=True)
    if len(result["summary"]) > 500 or len(result["hypotheses"]) > 1:
        raise ValueError("Diagnosis output exceeds bounds")
    if not result["unresolved_questions"] or len(result["unresolved_questions"]) > 6:
        raise ValueError("Diagnosis needs bounded unresolved questions")
    if any(len(question) > 200 for question in result["unresolved_questions"]):
        raise ValueError("Diagnosis question exceeds bounds")

    diagnosis = validate_diagnosis({**result, "source": "llm"}, state)
    if diagnosis["status"] == "inconclusive":
        if diagnosis["hypotheses"]:
            raise ValueError("Inconclusive diagnosis cannot assert a cause")
        return diagnosis

    pair = _database_auth_evidence(state)
    hypothesis = diagnosis["hypotheses"][0]
    cause = hypothesis["cause"].lower()
    if (
        not pair
        or not set(pair) <= set(hypothesis["supporting_evidence_ids"])
        or hypothesis.get("contradicting_evidence_ids")
        or not ("login" in cause or "auth" in cause)
        or not ("reject" in cause or "invalid_password" in cause)
    ):
        raise ValueError("Supported mechanism is not established by current evidence")
    # The model selects and cites the mechanism; policy owns the final wording.
    diagnosis["summary"] = (
        "API database connections fail because database login is rejected."
    )
    diagnosis["hypotheses"] = [
        {
            "cause": (
                "Database authentication rejection prevents the API "
                "from opening database connections."
            ),
            "supporting_evidence_ids": pair,
        }
    ]
    return validate_diagnosis(diagnosis, state)


def _failure_reason(error: Exception) -> DiagnosisFailure:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "provider_unavailable"
    if isinstance(error, TemporaryProviderError):
        return error.reason
    if isinstance(error, (ValidationError, ValueError)):
        return "invalid_output"
    return "provider_error"


def _inconclusive_fallback(state: InvestigationState, reason: DiagnosisFailure) -> dict:
    baseline = diagnose_baseline(state)["diagnosis"]
    diagnosis = {
        **baseline,
        "status": "inconclusive",
        "summary": (
            "Model diagnosis failed; current diagnostics do not establish a cause."
        ),
        "hypotheses": [],
        "source": "fallback",
    }
    return {
        "diagnosis": validate_diagnosis(diagnosis, state),
        "diagnosis_source": "fallback",
        "diagnosis_failure_reason": reason,
    }


def build_llm_diagnose_node(
    model: DiagnosisModel,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[InvestigationState], dict]:
    """Use bounded provider calls, one schema repair, and safe fallback."""
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 3
        or isinstance(base_delay, bool)
        or not isinstance(base_delay, (int, float))
        or not math.isfinite(base_delay)
        or base_delay < 0
    ):
        raise ValueError("Invalid model diagnosis retry settings")

    def diagnose_model(state: InvestigationState) -> dict:
        payload = _model_input(state)
        calls = 0
        repair_attempted = False
        started = time.perf_counter()

        def invoke(request: str) -> object:
            nonlocal calls
            for attempt in range(max_attempts):
                calls += 1
                try:
                    return model.invoke(request)
                except (TimeoutError, ConnectionError, TemporaryProviderError):
                    if attempt + 1 == max_attempts:
                        raise
                    sleep(base_delay * 2**attempt)
            raise AssertionError("Retry loop exhausted")

        try:
            try:
                diagnosis = _validated_model_diagnosis(invoke(payload), state)
            except (ValidationError, ValueError) as error:
                repair_attempted = True
                details = (
                    error.errors(include_input=False)
                    if isinstance(error, ValidationError)
                    else [{"message": str(error)}]
                )
                repair = (
                    "Correct the structured diagnosis for the ORIGINAL input below. "
                    "Use only current_evidence for current findings and citations. "
                    "History and failures are not supporting evidence. Return only "
                    "the JSON schema fields, without source or severity.\n"
                    f"Schema: {json.dumps(_model_diagnosis_adapter.json_schema())}\n"
                    f"Original input: {payload}\n"
                    f"Validation errors: {json.dumps(details)}"
                )
                diagnosis = _validated_model_diagnosis(invoke(repair), state)
        except Exception as error:
            update = _inconclusive_fallback(state, _failure_reason(error))
        else:
            update = {
                "diagnosis": diagnosis,
                "diagnosis_source": "llm",
                "diagnosis_failure_reason": None,
            }
        return {
            **update,
            "diagnosis_model_calls": calls,
            "diagnosis_repair_attempted": repair_attempted,
            "diagnosis_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    return diagnose_model
