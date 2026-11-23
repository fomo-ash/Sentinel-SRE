"""Compare three simulated-investigation routes on fixed, hidden labels."""

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from evaluation.evaluate_classification import load_env_file
from sentinel.graph import build_graph
from sentinel.tools.diagnostics import DiagnosticReadError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "services/mock-service/internal/incidents/fixtures"
DEV_DATASET = Path(__file__).with_name("investigation_dev_v1.jsonl")
HOLDOUT_DATASET = Path(__file__).with_name("investigation_holdout_v1.jsonl")
_CASE_FIELDS = {
    "case_id",
    "incident_id",
    "service",
    "summary",
    "expected_severity",
    "expected_symptoms",
    "expected_status",
    "expected_mechanism",
    "expected_evidence_ids",
    "label_reason",
}


class FixtureDiagnosticClient:
    """Read the same embedded Go snapshots for every comparison arm."""

    def __init__(self, directory: Path = FIXTURES):
        self.snapshots = {}
        for kind in ("health", "metrics", "logs"):
            records = json.loads((directory / f"{kind}_v1.json").read_text())
            self.snapshots[kind] = {item["incident_id"]: item for item in records}
        ids = [set(self.snapshots[kind]) for kind in self.snapshots]
        if ids[0] != ids[1] or ids[0] != ids[2]:
            raise ValueError("Diagnostic fixtures have mismatched incident IDs")

    def read(self, kind, incident_id, service, *, limit=None):
        record = self.snapshots[kind].get(incident_id)
        if record is None:
            raise DiagnosticReadError("not_found")
        return deepcopy(record)


def load_cases(path: Path) -> list[dict]:
    """Reject malformed labels before any graph or provider call."""
    cases = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or set(case) != _CASE_FIELDS:
            raise ValueError(f"Case {number} has invalid fields")
        if any(
            not isinstance(case[key], str) or not case[key].strip()
            for key in ("case_id", "incident_id", "service", "summary", "label_reason")
        ):
            raise ValueError(f"Case {number} has missing incident fields")
        if case["case_id"] in seen:
            raise ValueError(f"Duplicate case ID: {case['case_id']}")
        seen.add(case["case_id"])
        if case["expected_severity"] not in ("unknown", "sev1", "sev2", "sev3"):
            raise ValueError(f"Case {number} has invalid severity")
        symptoms = case["expected_symptoms"]
        if not isinstance(symptoms, list) or any(
            item
            not in (
                "database_connectivity",
                "network_connectivity",
                "elevated_errors",
                "high_latency",
                "service_outage",
            )
            for item in symptoms
        ):
            raise ValueError(f"Case {number} has invalid symptoms")
        if case["expected_status"] not in ("supported", "inconclusive"):
            raise ValueError(f"Case {number} has invalid diagnosis status")
        expected_ids = case["expected_evidence_ids"]
        if not isinstance(expected_ids, list) or any(
            not isinstance(item, str) or not item.startswith(f"{case['incident_id']}:")
            for item in expected_ids
        ):
            raise ValueError(f"Case {number} has invalid evidence labels")
        if case["expected_status"] == "supported":
            if (
                case["expected_mechanism"] != "database_authentication_rejection"
                or len(expected_ids) < 2
            ):
                raise ValueError(f"Case {number} needs supported-cause labels")
        elif case["expected_mechanism"] is not None or expected_ids:
            raise ValueError(f"Case {number} has cause labels for abstention")
        cases.append(case)
    if not cases:
        raise ValueError("Investigation dataset must not be empty")
    return cases


def _usage(model) -> dict[str, int] | None:
    value = getattr(model, "token_usage", None)
    keys = ("input_tokens", "output_tokens", "calls_with_usage")
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), int) for key in keys
    ):
        return None
    return {key: value[key] for key in keys}


def _delta(before, after):
    if before is None or after is None:
        return None
    return {key: after[key] - before[key] for key in before}


def _p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def _score(cases: list[dict], outputs: list[dict]) -> dict:
    total = len(cases)
    severity_hits = status_hits = supported_hits = abstention_hits = 0
    supported_count = abstention_count = tp = fp = fn = 0
    invalid_citations = unsupported_claims = fallbacks = repairs = 0
    failure_reasons: dict[str, int] = {}
    mismatched_ids = []
    for case, output in zip(cases, outputs, strict=True):
        severity_hits += output["final_severity"] == case["expected_severity"]
        status_hits += output["diagnosis_status"] == case["expected_status"]
        expected = set(case["expected_symptoms"])
        observed = set(output["symptoms"])
        tp += len(expected & observed)
        fp += len(observed - expected)
        fn += len(expected - observed)
        if case["expected_status"] == "supported":
            supported_count += 1
            supported_hits += output["diagnosis_status"] == "supported" and set(
                case["expected_evidence_ids"]
            ) <= set(output["supporting_evidence_ids"])
        else:
            abstention_count += 1
            abstention_hits += output["diagnosis_status"] == "inconclusive"
        invalid_citations += not output["citations_valid"]
        unsupported_claims += (
            output["diagnosis_status"] == "supported"
            and case["expected_status"] != "supported"
        )
        fallbacks += output["diagnosis_source"] == "fallback"
        repairs += output["diagnosis_repair_attempted"]
        reason = output["diagnosis_failure_reason"]
        if reason is not None:
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        if (
            output["final_severity"] != case["expected_severity"]
            or output["diagnosis_status"] != case["expected_status"]
        ):
            mismatched_ids.append(case["case_id"])
    return {
        "count": total,
        "severity_accuracy": severity_hits / total,
        "symptom_precision": tp / (tp + fp) if tp + fp else 0.0,
        "symptom_recall": tp / (tp + fn) if tp + fn else 0.0,
        "diagnosis_status_accuracy": status_hits / total,
        "supported_cause_recall": (
            supported_hits / supported_count if supported_count else None
        ),
        "correct_abstention_rate": (
            abstention_hits / abstention_count if abstention_count else None
        ),
        "invalid_citation_count": invalid_citations,
        "unsupported_claim_count": unsupported_claims,
        "fallback_rate": fallbacks / total,
        "accepted_model_diagnosis_rate": (
            sum(item["diagnosis_source"] == "llm" for item in outputs) / total
        ),
        "classification_fallback_rate": (
            sum(
                item["classification_source"] == "baseline_fallback" for item in outputs
            )
            / total
        ),
        "repair_rate": repairs / total,
        "failure_reasons": failure_reasons,
        "mean_total_latency_ms": statistics.mean(
            item["total_latency_ms"] for item in outputs
        ),
        "p95_total_latency_ms": _p95([item["total_latency_ms"] for item in outputs]),
        "mean_diagnostic_calls": statistics.mean(
            item["tool_call_count"] for item in outputs
        ),
        "mean_model_diagnosis_calls": statistics.mean(
            item["diagnosis_model_calls"] for item in outputs
        ),
        "mismatched_case_ids": mismatched_ids,
    }


def evaluate_dataset(
    cases, classifier_model, diagnosis_model, retriever, client
) -> dict:
    """Run all arms on identical inputs; labels never reach a graph."""
    if not cases:
        raise ValueError("Investigation dataset must not be empty")
    graphs = {
        "baseline": build_graph(diagnostic_client=client),
        "model": build_graph(
            classifier_model,
            diagnostic_client=client,
            diagnosis_model=diagnosis_model,
        ),
        "model_history": build_graph(
            classifier_model,
            retriever=retriever,
            diagnostic_client=client,
            diagnosis_model=diagnosis_model,
        ),
    }
    results = {"cases": [], "arms": {}}
    arm_outputs = {}
    for arm, graph in graphs.items():
        classifier_before = _usage(classifier_model)
        diagnosis_before = _usage(diagnosis_model)
        outputs = []
        for case in cases:
            incident = {
                "id": case["incident_id"],
                "service": case["service"],
                "summary": case["summary"],
            }
            config = {
                "configurable": {
                    "thread_id": f"phase5-{arm}-{case['case_id']}",
                    "classification_mode": "deterministic"
                    if arm == "baseline"
                    else "llm",
                    "retrieval_mode": "history" if arm == "model_history" else "off",
                    "investigation_mode": "simulated",
                    "diagnosis_mode": "deterministic" if arm == "baseline" else "llm",
                }
            }
            started = time.perf_counter()
            state = graph.invoke({"incident": incident}, config)
            diagnosis = state["diagnosis"]
            cited = [
                evidence_id
                for hypothesis in diagnosis["hypotheses"]
                for evidence_id in hypothesis["supporting_evidence_ids"]
            ]
            current_ids = {
                item["id"]
                for item in state["evidence"]
                if item["incident_id"] == incident["id"]
                and item["service"] == incident["service"]
            }
            outputs.append(
                {
                    "case_id": case["case_id"],
                    "thread_id": config["configurable"]["thread_id"],
                    "final_severity": state["final_severity"],
                    "symptoms": state["symptoms"],
                    "classification_source": state["classification_source"],
                    "classification_failure_reason": state["failure_reason"],
                    "diagnosis_status": diagnosis["status"],
                    "diagnosis_source": state["diagnosis_source"],
                    "diagnosis_failure_reason": state["diagnosis_failure_reason"],
                    "diagnosis_summary": diagnosis["summary"],
                    "unresolved_questions": diagnosis["unresolved_questions"],
                    "supporting_evidence_ids": cited,
                    "citations_valid": set(cited) <= current_ids,
                    "current_evidence_ids": sorted(current_ids),
                    "historical_match_ids": [
                        item["id"] for item in state["historical_matches"]
                    ],
                    "retrieval_failure_reason": state["retrieval_failure_reason"],
                    "investigation_stop_reason": state["investigation_stop_reason"],
                    "tool_call_count": state["tool_call_count"],
                    "diagnostic_latency_ms": state["diagnostic_latency_ms"],
                    "diagnosis_model_calls": state["diagnosis_model_calls"],
                    "diagnosis_repair_attempted": state["diagnosis_repair_attempted"],
                    "diagnosis_latency_ms": state["diagnosis_latency_ms"],
                    "total_latency_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                }
            )
        arm_outputs[arm] = outputs
        metrics = _score(cases, outputs)
        metrics["classifier_token_usage"] = _delta(
            classifier_before, _usage(classifier_model)
        )
        metrics["diagnoser_token_usage"] = _delta(
            diagnosis_before, _usage(diagnosis_model)
        )
        results["arms"][arm] = metrics
    for index, case in enumerate(cases):
        results["cases"].append(
            {
                "case_id": case["case_id"],
                "expected_severity": case["expected_severity"],
                "expected_symptoms": case["expected_symptoms"],
                "expected_status": case["expected_status"],
                "expected_mechanism": case["expected_mechanism"],
                "label_reason": case["label_reason"],
                "baseline": arm_outputs["baseline"][index],
                "model": arm_outputs["model"][index],
                "model_history": arm_outputs["model_history"][index],
            }
        )
        evidence_sets = [
            arm_outputs[arm][index]["current_evidence_ids"] for arm in graphs
        ]
        if not all(items == evidence_sets[0] for items in evidence_sets):
            raise ValueError("Comparison arms saw different diagnostic evidence")
    return results


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEV_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--retrieval-model", type=str)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("pass --live to explicitly allow provider calls")
    if args.env_file is not None:
        load_env_file(args.env_file)

    from retrieval.embeddings import EmbeddingModel
    from retrieval.retriever import HistoricalIncidentRetriever
    from sentinel.openai_classifier import create_openai_classifier
    from sentinel.openai_diagnoser import create_openai_diagnoser

    embedding = (
        EmbeddingModel(args.retrieval_model)
        if args.retrieval_model
        else EmbeddingModel()
    )
    retriever = HistoricalIncidentRetriever(embedding_model=embedding)
    dataset = load_cases(args.dataset)
    results = evaluate_dataset(
        dataset,
        create_openai_classifier(),
        create_openai_diagnoser(),
        retriever,
        FixtureDiagnosticClient(),
    )
    results["run"] = {
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset),
        "dataset_sha256": _sha256(args.dataset),
        "fixture_sha256": {
            kind: _sha256(FIXTURES / f"{kind}_v1.json")
            for kind in ("health", "metrics", "logs")
        },
        "model_id": os.environ["SENTINEL_OPENAI_MODEL"],
        "retrieval_model": args.retrieval_model
        or "sentence-transformers/all-MiniLM-L6-v2",
        "label_review": "project-authored; no independent reviewer",
    }
    output = json.dumps(results, indent=2) + "\n"
    if args.output is None:
        print(output)
    else:
        args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
