"""Bounded graph transitions for opt-in simulated investigation."""

from copy import deepcopy

import pytest

from sentinel.graph import build_graph
from sentinel.tools.diagnostics import DiagnosticReadError

_BASE = {
    "incident_id": "diag-degraded",
    "service": "api",
    "observed_at": "2026-01-01T00:05:00Z",
}
_SNAPSHOTS = {
    "health": {
        **_BASE,
        "status": "degraded",
        "checks": [
            {"id": "readiness", "status": "degraded", "detail": "Probe was slow."}
        ],
    },
    "metrics": {
        **_BASE,
        "metrics": [
            {
                "id": "http_error_ratio",
                "name": "http_error_ratio",
                "value": 0.12,
                "unit": "ratio",
                "window": "5m",
            }
        ],
    },
    "logs": {
        **_BASE,
        "entries": [
            {
                "id": "timeout",
                "timestamp": "2026-01-01T00:04:00Z",
                "component": "api",
                "level": "error",
                "message": "A request timed out.",
            }
        ],
    },
}


class FakeClient:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}

    def read(self, kind, incident_id, service, *, limit=None):
        self.calls.append((kind, incident_id, service, limit))
        queued = self.outcomes.get(kind, [])
        if queued:
            result = queued.pop(0)
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)
        return deepcopy(_SNAPSHOTS[kind])


def _invoke(graph, *, mode="simulated", thread_id="diag-run"):
    config = {"configurable": {"thread_id": thread_id, "investigation_mode": mode}}
    result = graph.invoke(
        {
            "incident": {
                "id": "diag-degraded",
                "service": "api",
                "summary": "API requests are degraded for several users.",
            }
        },
        config,
    )
    return result, graph.get_state(config).values


def test_opt_in_reads_three_routes_and_checkpoints_current_evidence():
    client = FakeClient()
    graph = build_graph(diagnostic_client=client)
    result, checkpoint = _invoke(graph)
    assert [call[0] for call in client.calls] == ["health", "metrics", "logs"]
    assert client.calls[-1][-1] == 20
    assert result["investigation_round"] == 3
    assert result["tool_call_count"] == 3
    assert result["diagnostic_latency_ms"] >= 0
    assert result["completed_checks"] == ["health", "metrics", "logs"]
    assert result["investigation_stop_reason"] == "checks_complete"
    assert result["diagnostic_failures"] == []
    assert [item["id"] for item in result["evidence"]] == [
        "diag-degraded:health:readiness",
        "diag-degraded:metric:http_error_ratio",
        "diag-degraded:log:timeout",
    ]
    assert all(item["incident_id"] == "diag-degraded" for item in result["evidence"])
    assert all(item["service"] == "api" for item in result["evidence"])
    assert all(
        item["source_ref"].startswith("GET /incidents/") for item in result["evidence"]
    )
    baseline, _ = _invoke(build_graph(), mode="off", thread_id="baseline")
    assert result["final_severity"] == baseline["final_severity"]
    assert "diagnosis" not in result
    assert checkpoint == result


def test_default_route_never_calls_diagnostic_client():
    client = FakeClient()
    result, _ = _invoke(build_graph(diagnostic_client=client), mode="off")
    assert client.calls == []
    assert result["tool_call_count"] == 0
    assert result["evidence"] == []
    assert result["investigation_stop_reason"] is None


def test_not_found_stops_after_one_attempt_without_evidence():
    client = FakeClient({"health": [DiagnosticReadError("not_found")]})
    result, _ = _invoke(build_graph(diagnostic_client=client))
    assert len(client.calls) == 1
    assert result["tool_call_count"] == 1
    assert result["investigation_round"] == 1
    assert result["investigation_stop_reason"] == "incident_not_found"
    assert result["evidence"] == []
    assert result["diagnostic_failures"][0]["category"] == "not_found"


def test_transient_failure_retries_once_and_counts_failed_attempt():
    client = FakeClient({"health": [DiagnosticReadError("timeout")]})
    result, _ = _invoke(build_graph(diagnostic_client=client))
    assert [call[0] for call in client.calls] == ["health", "health", "metrics", "logs"]
    assert result["tool_call_count"] == 4
    assert result["investigation_round"] == 3
    assert result["diagnostic_failures"][0]["category"] == "timeout"
    assert result["diagnostic_failures"][0]["attempts"] == 1
    assert len(result["evidence"]) == 3


def test_call_budget_blocks_next_read_after_exhausted_retries():
    client = FakeClient(
        {"health": [DiagnosticReadError("timeout"), DiagnosticReadError("timeout")]}
    )
    result, _ = _invoke(build_graph(diagnostic_client=client, max_tool_calls=2))
    assert [call[0] for call in client.calls] == ["health", "health"]
    assert result["tool_call_count"] == 2
    assert result["investigation_round"] == 1
    assert result["investigation_stop_reason"] == "max_tool_calls"
    assert result["evidence"] == []
    assert [item["attempts"] for item in result["diagnostic_failures"]] == [1, 2]


def test_round_budget_stops_before_second_read():
    client = FakeClient()
    result, _ = _invoke(
        build_graph(diagnostic_client=client, max_investigation_rounds=1)
    )
    assert [call[0] for call in client.calls] == ["health"]
    assert result["tool_call_count"] == 1
    assert result["investigation_stop_reason"] == "max_rounds"


def test_empty_observations_stop_without_fabricated_evidence():
    client = FakeClient(
        {
            "health": [{**_BASE, "status": "unknown", "checks": []}],
            "metrics": [{**_BASE, "metrics": []}],
            "logs": [{**_BASE, "entries": []}],
        }
    )
    result, _ = _invoke(build_graph(diagnostic_client=client))
    assert result["tool_call_count"] == 3
    assert result["investigation_stop_reason"] == "no_usable_data"
    assert result["evidence"] == []


def test_invalid_response_is_not_retried_or_recorded_as_evidence():
    client = FakeClient({"health": [{**_SNAPSHOTS["health"], "service": "worker"}]})
    result, _ = _invoke(build_graph(diagnostic_client=client))
    assert [call[0] for call in client.calls] == ["health", "metrics", "logs"]
    assert result["diagnostic_failures"][0]["category"] == "invalid_response"
    assert len(result["evidence"]) == 2
    assert all(item["kind"] != "health" for item in result["evidence"])


def test_historical_match_never_becomes_current_evidence():
    class FakeRetriever:
        def retrieve(self, query, top_k=3):
            return [
                {
                    "id": "hist-one",
                    "score": 0.8,
                    "historical_incident": {
                        "id": "hist-one",
                        "summary": "A past API outage.",
                        "confirmed_cause": "Past queue failure.",
                        "resolution": "Past queue repair.",
                        "provenance": {"type": "synthetic"},
                    },
                }
            ]

    client = FakeClient(
        {
            "health": [{**_BASE, "status": "unknown", "checks": []}],
            "metrics": [{**_BASE, "metrics": []}],
            "logs": [{**_BASE, "entries": []}],
        }
    )
    result = build_graph(retriever=FakeRetriever(), diagnostic_client=client).invoke(
        {
            "incident": {
                "id": "diag-degraded",
                "service": "api",
                "summary": "Impact under investigation",
            }
        },
        {
            "configurable": {
                "thread_id": "history-with-investigation",
                "retrieval_mode": "history",
                "investigation_mode": "simulated",
            }
        },
    )
    assert result["historical_matches"][0]["id"] == "hist-one"
    assert result["evidence"] == []
    assert result["investigation_stop_reason"] == "no_usable_data"


def test_bad_mode_client_or_id_fails_before_diagnostic_call():
    client = FakeClient()
    with pytest.raises(ValueError, match="Unsupported investigation mode"):
        _invoke(build_graph(diagnostic_client=client), mode="live")
    with pytest.raises(ValueError, match="requires an injected client"):
        _invoke(build_graph())
    graph = build_graph(diagnostic_client=client)
    with pytest.raises(ValueError, match="synthetic diagnostic ID"):
        graph.invoke(
            {"incident": {"id": "prod-1", "service": "api", "summary": "Alarm"}},
            {"configurable": {"thread_id": "prod", "investigation_mode": "simulated"}},
        )
    assert client.calls == []


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_investigation_rounds", 0),
        ("max_investigation_rounds", 4),
        ("max_tool_calls", 0),
        ("max_tool_calls", 7),
        ("max_attempts_per_read", 3),
        ("max_attempts_per_read", True),
    ],
)
def test_invalid_budgets_are_rejected_at_build_time(setting, value):
    with pytest.raises(ValueError, match=setting):
        build_graph(**{setting: value})
