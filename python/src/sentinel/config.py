"""Small environment based configuration; no secrets are needed yet."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mock_service_url: str = "http://127.0.0.1:8080"


def load_settings() -> Settings:
    return Settings(
        mock_service_url=os.getenv("SENTINEL_MOCK_SERVICE_URL", "http://127.0.0.1:8080")
    )
