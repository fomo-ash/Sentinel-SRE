"""Deterministic planning and bounded execution of simulated diagnostic reads."""

from time import perf_counter

from sentinel.schemas import DiagnosticCheck, InvestigationState
from sentinel.tools.diagnostics import (
    DiagnosticClient,
    DiagnosticReadError,
    validate_diagnostic_response,
)

DIAGNOSTIC_PLAN: list[DiagnosticCheck] = [
    {"kind": "health", "reason": "Read current simulated service health."},
    {"kind": "metrics", "reason": "Read current simulated metrics."},
    {"kind": "logs", "reason": "Read current simulated log entries."},
]


def plan_diagnostics(
    state: InvestigationState, *, max_rounds: int, max_tool_calls: int
) -> dict:
    """Choose the next allowlisted check or stop before exhausting a budget."""
    completed = state.get("completed_checks", [])
    remaining = [check for check in DIAGNOSTIC_PLAN if check["kind"] not in completed]
    if not remaining:
        reason = "checks_complete" if state.get("evidence") else "no_usable_data"
    elif state.get("investigation_round", 0) >= max_rounds:
        reason = "max_rounds"
    elif state.get("tool_call_count", 0) >= max_tool_calls:
        reason = "max_tool_calls"
    else:
        return {"pending_check": remaining[0], "investigation_stop_reason": None}
    return {"pending_check": None, "investigation_stop_reason": reason}


def run_diagnostic(
    state: InvestigationState,
    *,
    client: DiagnosticClient,
    max_tool_calls: int,
    max_attempts_per_read: int,
) -> dict:
    """Count every client attempt, including timeouts and recovered failures."""
    check = state["pending_check"]
    if check is None:
        raise ValueError("No diagnostic check is pending")
    kind = check["kind"]
    incident = state["incident"]
    calls = state.get("tool_call_count", 0)
    if calls >= max_tool_calls:
        raise ValueError("Diagnostic call budget was exhausted before execution")
    attempts = 0
    failures = list(state.get("diagnostic_failures", []))
    started = perf_counter()
    while attempts < max_attempts_per_read and calls < max_tool_calls:
        attempts += 1
        calls += 1
        attempt_started = perf_counter()
        try:
            result = client.read(
                kind,
                incident["id"],
                incident["service"],
                limit=20 if kind == "logs" else None,
            )
            result = validate_diagnostic_response(
                kind,
                incident["id"],
                incident["service"],
                result,
                limit=20 if kind == "logs" else None,
            )
        except DiagnosticReadError as exc:
            category = exc.category
        except TimeoutError:
            category = "timeout"
        except ConnectionError:
            category = "unavailable"
        except Exception:
            category = "invalid_response"
        else:
            elapsed = round((perf_counter() - started) * 1000, 3)
            return {
                "diagnostic_result": result,
                "diagnostic_error": None,
                "diagnostic_failures": failures,
                "last_attempts": attempts,
                "last_latency_ms": elapsed,
                "diagnostic_latency_ms": round(
                    state.get("diagnostic_latency_ms", 0.0) + elapsed, 3
                ),
                "tool_call_count": calls,
                "investigation_round": state.get("investigation_round", 0) + 1,
            }
        failures.append(
            {
                "kind": kind,
                "category": category,
                "attempts": attempts,
                "latency_ms": round((perf_counter() - attempt_started) * 1000, 3),
            }
        )
        if category not in ("timeout", "unavailable"):
            break
    elapsed = round((perf_counter() - started) * 1000, 3)
    return {
        "diagnostic_result": None,
        "diagnostic_error": category,
        "diagnostic_failures": failures,
        "last_attempts": attempts,
        "last_latency_ms": elapsed,
        "diagnostic_latency_ms": round(
            state.get("diagnostic_latency_ms", 0.0) + elapsed, 3
        ),
        "tool_call_count": calls,
        "investigation_round": state.get("investigation_round", 0) + 1,
    }
