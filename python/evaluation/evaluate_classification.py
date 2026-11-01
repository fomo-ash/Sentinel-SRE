"""Compare the default graph with an injected model on fixed incident labels."""

import argparse
import json
import math
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from sentinel.graph import build_graph
from sentinel.nodes.llm_classify import ClassificationModel

DATASET = Path(__file__).with_name("incidents_v1.jsonl")


def load_env_file(path: Path) -> None:
    """Read only the two optional provider settings from an explicit local file."""
    allowed = {"OPENAI_API_KEY", "SENTINEL_OPENAI_MODEL"}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_incidents(path: Path = DATASET) -> list[dict]:
    """Load labeled JSONL without passing labels to either classifier."""
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _score(records: list[dict], cases: list[dict], mode: str) -> dict:
    correct = true_positive = false_positive = false_negative = 0
    mismatched_ids: list[str] = []
    latencies: list[float] = []

    for record, case in zip(records, cases, strict=True):
        result = case[mode]
        expected_symptoms = set(record["expected_symptoms"])
        predicted_symptoms = set(result["symptoms"])
        if result["severity"] == record["expected_severity"]:
            correct += 1
        if (
            result["severity"] != record["expected_severity"]
            or predicted_symptoms != expected_symptoms
        ):
            mismatched_ids.append(record["id"])
        true_positive += len(predicted_symptoms & expected_symptoms)
        false_positive += len(predicted_symptoms - expected_symptoms)
        false_negative += len(expected_symptoms - predicted_symptoms)
        latencies.append(result["latency_ms"])

    ordered = sorted(latencies)
    return {
        "severity_accuracy": correct / len(records),
        "symptom_precision": (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        ),
        "symptom_recall": (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        ),
        "mean_latency_ms": statistics.mean(latencies),
        "p95_latency_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "mismatched_ids": mismatched_ids,
    }


def _usage(model: ClassificationModel) -> dict[str, int] | None:
    usage = getattr(model, "token_usage", None)
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "output_tokens", "calls_with_usage")
    if not all(isinstance(usage.get(key), int) for key in keys):
        return None
    return {key: usage[key] for key in keys}


def evaluate_dataset(
    records: list[dict],
    model: ClassificationModel,
    *,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> dict:
    """Run both routes with distinct checkpoint threads and compare outcomes."""
    if not records:
        raise ValueError("evaluation dataset must not be empty")
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("evaluation IDs must be unique")
    if (input_usd_per_million is None) != (output_usd_per_million is None):
        raise ValueError("both token prices must be provided together")
    if any(
        rate is not None and rate < 0
        for rate in (input_usd_per_million, output_usd_per_million)
    ):
        raise ValueError("token prices must be nonnegative")

    baseline_graph = build_graph()
    model_graph = build_graph(model)
    usage_before = _usage(model)
    cases: list[dict] = []

    for record in records:
        # The only classifier input is the incident; labels stay in this evaluator.
        incident = {key: record[key] for key in ("id", "service", "summary")}
        case: dict = {"id": record["id"]}
        for mode, graph in (("baseline", baseline_graph), ("model", model_graph)):
            config = {
                "configurable": {
                    "thread_id": f"eval-{mode}-{record['id']}",
                    "classification_mode": "llm"
                    if mode == "model"
                    else "deterministic",
                }
            }
            start = time.perf_counter()
            state = graph.invoke({"incident": incident}, config)
            latency_ms = (time.perf_counter() - start) * 1000
            interpretation = state.get("model_interpretation")
            case[mode] = {
                "severity": state["final_severity"],
                "symptoms": state["symptoms"],
                "source": state["classification_source"],
                "failure_reason": state["failure_reason"],
                "structured_output_valid": interpretation is not None,
                "recommended_severity": (
                    interpretation["recommended_severity"] if interpretation else None
                ),
                "source_references": (
                    interpretation["source_references"] if interpretation else []
                ),
                "latency_ms": latency_ms,
            }
        cases.append(case)

    baseline = _score(records, cases, "baseline")
    model_metrics = _score(records, cases, "model")
    count = len(records)
    model_metrics["structured_output_validity_rate"] = (
        sum(case["model"]["structured_output_valid"] for case in cases) / count
    )
    model_metrics["fallback_rate"] = (
        sum(case["model"]["source"] == "baseline_fallback" for case in cases) / count
    )
    model_metrics["failure_reasons"] = {
        reason: sum(case["model"]["failure_reason"] == reason for case in cases)
        for reason in sorted(
            {
                case["model"]["failure_reason"]
                for case in cases
                if case["model"]["failure_reason"] is not None
            }
        )
    }

    usage_after = _usage(model)
    if usage_before is not None and usage_after is not None:
        usage_delta = {
            key: usage_after[key] - usage_before[key] for key in usage_before
        }
        model_metrics["reported_token_usage"] = usage_delta
        if input_usd_per_million is not None and usage_delta["calls_with_usage"]:
            model_metrics["estimated_cost_usd"] = (
                usage_delta["input_tokens"] * input_usd_per_million
                + usage_delta["output_tokens"] * output_usd_per_million
            ) / 1_000_000
        else:
            model_metrics["estimated_cost_usd"] = None
    else:
        model_metrics["reported_token_usage"] = None
        model_metrics["estimated_cost_usd"] = None

    return {
        "count": count,
        "baseline": baseline,
        "model": model_metrics,
        "cases": cases,
    }


def main() -> None:
    """Run an explicit live comparison and print or save JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    args = parser.parse_args()
    if not args.live:
        parser.error("pass --live to explicitly allow provider calls")
    if args.env_file is not None:
        load_env_file(args.env_file)

    from sentinel.openai_classifier import create_openai_classifier

    results = evaluate_dataset(
        load_incidents(args.dataset),
        create_openai_classifier(),
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
    )
    results["run"] = {
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset),
        "model_id": os.environ["SENTINEL_OPENAI_MODEL"],
        "input_usd_per_million": args.input_usd_per_million,
        "output_usd_per_million": args.output_usd_per_million,
    }
    output = json.dumps(results, indent=2)
    if args.output is None:
        print(output)
    else:
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
