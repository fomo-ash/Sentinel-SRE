"""Small environment based configuration; no secrets are needed yet."""

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    mock_service_url: str = "http://127.0.0.1:8080"


def load_settings() -> Settings:
    return Settings(
        mock_service_url=os.getenv("SENTINEL_MOCK_SERVICE_URL", "http://127.0.0.1:8080")
    )


def validate_mock_service_origin(address: str) -> str:
    """Accept only an HTTP origin at a literal loopback IP and explicit port."""
    if not isinstance(address, str):
        raise ValueError("base_url must be a loopback HTTP origin")
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
    return address
