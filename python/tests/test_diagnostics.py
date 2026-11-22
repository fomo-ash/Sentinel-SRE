"""Local HTTP diagnostic contracts and failure categories."""

from copy import deepcopy

import httpx
import pytest

from sentinel.tools.diagnostics import (
    DiagnosticClient,
    DiagnosticReadError,
    HTTPDiagnosticClient,
)

_BASE = {
    "incident_id": "diag-degraded",
    "service": "api",
    "observed_at": "2026-01-01T00:05:00Z",
}
_RESPONSES = {
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
                "id": "queue_depth",
                "name": "queue_depth",
                "value": 230,
                "unit": "items",
                "window": "5m",
            }
        ],
    },
    "logs": {
        **_BASE,
        "entries": [
            {
                "id": "request-timeout",
                "timestamp": "2026-01-01T00:04:00Z",
                "component": "api",
                "level": "error",
                "message": "Request timed out.",
            }
        ],
    },
}


def _client(handler):
    return HTTPDiagnosticClient(transport=httpx.MockTransport(handler))


def test_reads_three_fixed_routes_and_validates_metadata():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.url.query))
        kind = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json=_RESPONSES[kind])

    with _client(handler) as client:
        for kind in ("health", "metrics", "logs"):
            result = client.read(kind, "diag-degraded", "api")
            assert result["incident_id"] == "diag-degraded"
            assert result["service"] == "api"
    assert seen == [
        ("/incidents/diag-degraded/health", b""),
        ("/incidents/diag-degraded/metrics", b""),
        ("/incidents/diag-degraded/logs", b"limit=20"),
    ]


def test_empty_observations_are_successful_missing_data():
    payload = {**_BASE, "entries": []}
    with _client(lambda request: httpx.Response(200, json=payload)) as client:
        assert client.read("logs", "diag-degraded", "api")["entries"] == []


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (404, "not_found"),
        (500, "unavailable"),
        (503, "unavailable"),
        (400, "invalid_response"),
        (302, "invalid_response"),
    ],
)
def test_http_failures_have_fixed_categories(status, category):
    with _client(lambda request: httpx.Response(status)) as client:
        with pytest.raises(DiagnosticReadError) as error:
            client.read("health", "diag-degraded", "api")
    assert error.value.category == category


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "unavailable"),
    ],
)
def test_transport_failures_have_fixed_categories(failure, category):
    def handler(request):
        raise failure("synthetic network failure", request=request)

    with _client(handler) as client:
        with pytest.raises(DiagnosticReadError) as error:
            client.read("health", "diag-degraded", "api")
    assert error.value.category == category
    assert str(error.value) == category


@pytest.mark.parametrize(
    "change",
    [
        {"incident_id": "diag-other"},
        {"service": "worker"},
        {"observed_at": "yesterday"},
        {"cause": "database"},
        {"checks": None},
    ],
)
def test_rejects_invalid_or_mismatched_health_response(change):
    payload = deepcopy(_RESPONSES["health"])
    payload.update(change)
    with _client(lambda request: httpx.Response(200, json=payload)) as client:
        with pytest.raises(DiagnosticReadError) as error:
            client.read("health", "diag-degraded", "api")
    assert error.value.category == "invalid_response"


def test_rejects_malformed_json_and_wrong_snapshot_shape():
    for response in (
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json=_RESPONSES["metrics"]),
    ):
        with _client(lambda request: response) as client:
            with pytest.raises(DiagnosticReadError) as error:
                client.read("health", "diag-degraded", "api")
        assert error.value.category == "invalid_response"


def test_rejects_bad_log_limit_and_extra_entries():
    payload = deepcopy(_RESPONSES["logs"])
    payload["entries"].append({**payload["entries"][0], "id": "second"})
    with _client(lambda request: httpx.Response(200, json=payload)) as client:
        with pytest.raises(DiagnosticReadError) as error:
            client.read("logs", "diag-degraded", "api", limit=1)
        assert error.value.category == "invalid_response"
        for limit in (0, 51, True):
            with pytest.raises(ValueError, match="log limit"):
                client.read("logs", "diag-degraded", "api", limit=limit)


def test_rejects_duplicate_and_future_log_entries():
    for change in ("duplicate", "future"):
        payload = deepcopy(_RESPONSES["logs"])
        if change == "duplicate":
            payload["entries"].append(deepcopy(payload["entries"][0]))
        else:
            payload["entries"][0]["timestamp"] = "2026-01-01T00:06:00Z"
        with _client(lambda request: httpx.Response(200, json=payload)) as client:
            with pytest.raises(DiagnosticReadError) as error:
                client.read("logs", "diag-degraded", "api")
        assert error.value.category == "invalid_response"


def test_rejects_unsafe_targets_and_requests_before_http():
    for base_url in (
        "https://127.0.0.1:8080",
        "http://example.com:8080",
        "http://127.0.0.1:8080/other",
        "http://user:pass@127.0.0.1:8080",
    ):
        with pytest.raises(ValueError, match="loopback HTTP origin"):
            HTTPDiagnosticClient(base_url)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_RESPONSES["health"])

    with _client(handler) as client:
        for kind, incident_id in (("other", "diag-degraded"), ("health", "../etc")):
            with pytest.raises(ValueError):
                client.read(kind, incident_id, "api")
    assert calls == []


def test_fake_client_can_implement_boundary_without_network():
    class FakeClient:
        def read(self, kind, incident_id, service, *, limit=None):
            return {"kind": kind, "incident_id": incident_id, "service": service}

    client: DiagnosticClient = FakeClient()
    assert client.read("health", "diag-degraded", "api")["kind"] == "health"
