"""Validated, read-only HTTP access to local synthetic incident diagnostics."""

import ipaddress
import math
import re
from datetime import datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from sentinel.config import load_settings
from sentinel.schemas import DiagnosticFailureCategory, DiagnosticKind

FailureCategory = DiagnosticFailureCategory

_INCIDENT_ID = re.compile(r"^diag-[a-z0-9]+(?:-[a-z0-9]+)*$")
_OBSERVATION_ID = r"^[a-z0-9][a-z0-9_-]*$"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires a timezone")
    return parsed


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _HealthCheck(_StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=_OBSERVATION_ID)
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    detail: str = Field(min_length=1, max_length=256)


class _Metric(_StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=_OBSERVATION_ID)
    name: str = Field(min_length=1, max_length=128)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=32)
    window: str = Field(min_length=1, max_length=32)


class _LogEntry(_StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=_OBSERVATION_ID)
    timestamp: str
    component: str = Field(min_length=1, max_length=64)
    level: Literal["debug", "info", "warn", "error"]
    message: str = Field(min_length=1, max_length=256)

    @field_validator("timestamp")
    @classmethod
    def valid_timestamp(cls, value: str) -> str:
        _parse_time(value)
        return value


class _Snapshot(_StrictModel):
    incident_id: str = Field(pattern=_INCIDENT_ID.pattern)
    service: str = Field(min_length=1, max_length=64)
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def valid_observed_at(cls, value: str) -> str:
        _parse_time(value)
        return value


class _HealthSnapshot(_Snapshot):
    status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    checks: list[_HealthCheck] = Field(max_length=20)


class _MetricsSnapshot(_Snapshot):
    metrics: list[_Metric] = Field(max_length=50)


class _LogsSnapshot(_Snapshot):
    entries: list[_LogEntry] = Field(max_length=50)


_MODELS = {
    "health": _HealthSnapshot,
    "metrics": _MetricsSnapshot,
    "logs": _LogsSnapshot,
}


class DiagnosticReadError(Exception):
    """Fixed failure category; raw HTTP details never enter graph state."""

    def __init__(self, category: FailureCategory):
        self.category = category
        super().__init__(category)


class DiagnosticClient(Protocol):
    """Fake-friendly boundary for one diagnostic read per graph action."""

    def read(
        self,
        kind: DiagnosticKind,
        incident_id: str,
        service: str,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]: ...


def validate_diagnostic_request(incident_id: str, service: str) -> None:
    """Keep synthetic IDs and service names out of caller-built URL paths."""
    if not isinstance(incident_id, str) or not _INCIDENT_ID.fullmatch(incident_id):
        raise ValueError("incident_id must be a synthetic diagnostic ID")
    if not isinstance(service, str) or not service.strip():
        raise ValueError("service must be nonempty")


def validate_diagnostic_response(
    kind: DiagnosticKind,
    incident_id: str,
    service: str,
    data: Any,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Apply the same response contract to HTTP and injected fake clients."""
    if kind not in _MODELS:
        raise DiagnosticReadError("invalid_response")
    try:
        snapshot = _MODELS[kind].model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise DiagnosticReadError("invalid_response") from exc
    if snapshot.incident_id != incident_id or snapshot.service != service:
        raise DiagnosticReadError("invalid_response")
    items = {
        "health": getattr(snapshot, "checks", []),
        "metrics": getattr(snapshot, "metrics", []),
        "logs": getattr(snapshot, "entries", []),
    }[kind]
    if len({item.id for item in items}) != len(items):
        raise DiagnosticReadError("invalid_response")
    if kind == "health" and not items and snapshot.status != "unknown":
        raise DiagnosticReadError("invalid_response")
    if kind == "logs" and any(
        _parse_time(entry.timestamp) > _parse_time(snapshot.observed_at)
        for entry in items
    ):
        raise DiagnosticReadError("invalid_response")
    if kind == "logs" and limit is not None and len(items) > limit:
        raise DiagnosticReadError("invalid_response")
    return snapshot.model_dump(mode="json")


class HTTPDiagnosticClient:
    """Read the three allowlisted routes from a loopback-only Go service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        address = base_url if base_url is not None else load_settings().mock_service_url
        try:
            parsed = urlsplit(address)
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise ValueError("base_url must be a loopback HTTP origin") from exc
        if (
            parsed.scheme != "http"
            or not loopback
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be a loopback HTTP origin")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive finite number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self._client = httpx.Client(
            base_url=address,
            timeout=timeout,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HTTPDiagnosticClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(
        self,
        kind: DiagnosticKind,
        incident_id: str,
        service: str,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(kind, str) or kind not in _MODELS:
            raise ValueError("kind must be health, metrics, or logs")
        validate_diagnostic_request(incident_id, service)
        if kind != "logs" and limit is not None:
            raise ValueError("limit applies only to logs")
        if kind == "logs":
            limit = 20 if limit is None else limit
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 50
            ):
                raise ValueError("log limit must be between 1 and 50")

        try:
            response = self._client.get(
                f"/incidents/{incident_id}/{kind}",
                params={"limit": limit} if kind == "logs" else None,
            )
        except httpx.TimeoutException as exc:
            raise DiagnosticReadError("timeout") from exc
        except httpx.RequestError as exc:
            raise DiagnosticReadError("unavailable") from exc

        if response.status_code == 404:
            raise DiagnosticReadError("not_found")
        if 500 <= response.status_code <= 599:
            raise DiagnosticReadError("unavailable")
        if response.status_code != 200:
            raise DiagnosticReadError("invalid_response")
        try:
            data = response.json()
        except ValueError as exc:
            raise DiagnosticReadError("invalid_response") from exc
        return validate_diagnostic_response(
            kind, incident_id, service, data, limit=limit
        )
