"""Load and validate the separate synthetic incident history corpus."""

import json
from pathlib import Path

DEFAULT_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "history" / "incidents_v1.jsonl"
)
TEXT_FIELDS = ("id", "service", "summary", "confirmed_cause", "resolution", "impact")
PROVENANCE_FIELDS = ("source", "verification")


def validate_history(
    historical_incidents: list[dict[str, object]],
) -> tuple[list[str], list[str]]:
    """Return IDs and summaries after validating every history record.

    The two lists retain file order so a future retrieval index can pair each
    searchable summary with its stable record ID.
    """
    if not historical_incidents:
        raise ValueError("History must contain at least one incident")

    ids: list[str] = []
    summaries: list[str] = []
    seen_ids: set[str] = set()

    for index, incident in enumerate(historical_incidents, start=1):
        if not isinstance(incident, dict):
            raise ValueError(f"History record {index} must be a JSON object")

        for field in TEXT_FIELDS:
            value = incident.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"History record {index} needs a non-empty {field}")

        incident_id = incident["id"]
        assert isinstance(incident_id, str)  # Checked above.
        if incident_id in seen_ids:
            raise ValueError(f"Duplicate history id: {incident_id}")
        seen_ids.add(incident_id)

        symptoms = incident.get("symptoms")
        if (
            not isinstance(symptoms, list)
            or not symptoms
            or any(not isinstance(item, str) or not item.strip() for item in symptoms)
        ):
            raise ValueError(f"History record {index} needs non-empty string symptoms")

        provenance = incident.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("type") != "synthetic":
            raise ValueError(f"History record {index} needs synthetic provenance")
        for field in PROVENANCE_FIELDS:
            value = provenance.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"History record {index} needs provenance.{field}")

        summary = incident["summary"]
        assert isinstance(summary, str)  # Checked above.
        ids.append(incident_id)
        summaries.append(summary)

    return ids, summaries


def load_history(
    path: str | Path = DEFAULT_HISTORY_PATH,
) -> list[dict[str, object]]:
    """Read JSONL history and reject malformed or incomplete records."""
    history_path = Path(path)
    incidents: list[dict[str, object]] = []

    with history_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                incident = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{history_path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(incident, dict):
                raise ValueError(
                    f"{history_path}:{line_number}: "
                    "history record must be a JSON object"
                )
            incidents.append(incident)

    validate_history(incidents)
    return incidents
