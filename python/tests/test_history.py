"""History loading must fail clearly before records enter retrieval."""

import importlib.util
import json
from pathlib import Path

import pytest


def load_history_module():
    path = Path(__file__).resolve().parents[1] / "retrieval" / "history.py"
    spec = importlib.util.spec_from_file_location("history", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


history = load_history_module()
load_history = history.load_history
validate_history = history.validate_history


def test_load_history_corpus() -> None:
    incidents = load_history()
    ids, summaries = validate_history(incidents)

    assert len(incidents) == len(ids) == len(summaries) == 101
    assert ids[0] == "hist-001"
    assert len(ids) == len(set(ids))
    assert summaries[0] == incidents[0]["summary"]


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("id", ""),
        ("summary", None),
        ("symptoms", []),
        ("symptoms", ["valid", ""]),
        ("provenance", {"type": "real"}),
    ],
)
def test_validate_history_rejects_invalid_record(field: str, invalid: object) -> None:
    incident = load_history()[0].copy()
    incident[field] = invalid

    with pytest.raises(ValueError, match="History record 1"):
        validate_history([incident])


def test_validate_history_rejects_duplicate_ids() -> None:
    incident = load_history()[0]

    with pytest.raises(ValueError, match="Duplicate history id: hist-001"):
        validate_history([incident, incident.copy()])


def test_load_history_reports_malformed_json_line(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("\n" + json.dumps(load_history()[0]) + "\n{broken\n")

    with pytest.raises(ValueError, match=r"bad\.jsonl:3: invalid JSON"):
        load_history(path)


def test_load_history_rejects_empty_file(tmp_path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")

    with pytest.raises(ValueError, match="at least one incident"):
        load_history(path)
