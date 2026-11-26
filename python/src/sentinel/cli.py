"""Run read-only investigations or an interactive local remediation review."""

import argparse
import json
import sys
from collections.abc import Callable
from uuid import uuid4

from langgraph.types import Command

from sentinel.graph import build_graph
from sentinel.tools.diagnostics import DiagnosticClient, HTTPDiagnosticClient
from sentinel.tools.remediation import HTTPRemediationClient, RemediationClient


def _incident(args: argparse.Namespace) -> dict:
    return {
        "incident": {
            "id": args.id,
            "service": args.service,
            "summary": args.summary,
        }
    }


def _read_only(args: argparse.Namespace, client: DiagnosticClient) -> dict:
    graph = build_graph(diagnostic_client=client)
    result = graph.invoke(
        _incident(args),
        {
            "configurable": {
                "thread_id": str(uuid4()),
                "investigation_mode": "simulated",
            }
        },
    )
    return {
        "incident": result["incident"],
        "final_severity": result["final_severity"],
        "investigation_stop_reason": result["investigation_stop_reason"],
        "investigation_round": result["investigation_round"],
        "tool_call_count": result["tool_call_count"],
        "diagnostic_latency_ms": result["diagnostic_latency_ms"],
        "evidence": result["evidence"],
        "diagnostic_failures": result["diagnostic_failures"],
        "diagnosis": result["diagnosis"],
    }


def _decision_response(
    prompt: dict, read_input: Callable[[str], str], emit: Callable[[str], None]
) -> dict | None:
    """Collect a human decision; empty, invalid, or closed input never approves."""
    for _ in range(3):
        try:
            decision = read_input("Decision (approve/reject): ").strip().lower()
            if decision not in ("approve", "reject"):
                emit("Enter exactly 'approve' or 'reject'.")
                continue
            operator_id = read_input("Operator ID: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not operator_id or len(operator_id) > 128:
            emit("Operator ID must be 1 to 128 nonblank characters.")
            continue
        proposal = prompt["proposal"]
        return {
            "thread_id": prompt["thread_id"],
            "proposal_id": proposal["proposal_id"],
            "proposal_fingerprint": proposal["fingerprint"],
            "incident_id": proposal["incident_id"],
            "service": proposal["service"],
            "decision": decision,
            "operator_id": operator_id,
        }
    return None


def run_operator_flow(
    args: argparse.Namespace,
    diagnostic_client: DiagnosticClient,
    remediation_client: RemediationClient,
    *,
    read_input: Callable[[str], str],
    emit: Callable[[str], None],
) -> int:
    """Keep the graph and its process-local checkpoint alive during review."""
    graph = build_graph(
        diagnostic_client=diagnostic_client,
        remediation_client=remediation_client,
    )
    config = {
        "configurable": {
            "thread_id": str(uuid4()),
            "investigation_mode": "simulated",
            "remediation_mode": "simulated",
        }
    }
    result = graph.invoke(_incident(args), config)
    if "__interrupt__" in result:
        prompt = result["__interrupt__"][0].value
        checkpoint = graph.get_state(config)
        if (
            checkpoint.next != ("approval",)
            or checkpoint.values.get("remediation_outcome") != "awaiting_approval"
            or checkpoint.values.get("approval_decision") is not None
            or checkpoint.values.get("execution_receipt") is not None
            or checkpoint.values.get("action_proposal") != prompt.get("proposal")
        ):
            raise ValueError("Approval checkpoint does not match the proposal")
        emit(
            json.dumps(
                {
                    "thread_id": prompt["thread_id"],
                    "proposal": prompt["proposal"],
                    "current_evidence_ids": prompt["current_evidence_ids"],
                    "warning": prompt["warning"],
                    "checkpoint_outcome": "awaiting_approval",
                    "approval_recorded": False,
                    "action_receipt_recorded": False,
                },
                indent=2,
            )
        )
        response = _decision_response(prompt, read_input, emit)
        if response is None:
            emit("Review ended without a decision. No action was requested.")
            return 2
        result = graph.invoke(Command(resume=response), config)
        if "__interrupt__" in result:
            emit("Decision was not accepted. No action was requested.")
            return 2
    report = result.get("remediation_report")
    if report is None:
        raise ValueError("Remediation flow ended without a report")
    emit(
        json.dumps(
            {"thread_id": config["configurable"]["thread_id"], "report": report},
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Investigate a synthetic incident through the local mock service."
    )
    parser.add_argument("--id", required=True, help="Synthetic diag- incident ID")
    parser.add_argument("--service", required=True, help="Service in the fixture")
    parser.add_argument("--summary", required=True, help="Reported incident summary")
    parser.add_argument(
        "--simulated-remediation",
        action="store_true",
        help="Interactively review one local synthetic action",
    )
    args = parser.parse_args(argv)

    if args.simulated_remediation and not sys.stdin.isatty():
        parser.error("simulated remediation requires an interactive terminal")
    try:
        with HTTPDiagnosticClient() as diagnostic_client:
            if args.simulated_remediation:
                # This client is constructed only after explicit CLI opt-in.
                with HTTPRemediationClient("diag-db-auth") as action_client:
                    return run_operator_flow(
                        args,
                        diagnostic_client,
                        action_client,
                        read_input=input,
                        emit=print,
                    )
            report = _read_only(args, diagnostic_client)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
