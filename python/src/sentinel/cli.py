"""Run a local, read-only simulated incident investigation."""

import argparse
import json
from uuid import uuid4

from sentinel.graph import build_graph
from sentinel.tools.diagnostics import HTTPDiagnosticClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Investigate a synthetic incident through the local mock service."
    )
    parser.add_argument("--id", required=True, help="Synthetic diag- incident ID")
    parser.add_argument("--service", required=True, help="Service in the fixture")
    parser.add_argument("--summary", required=True, help="Reported incident summary")
    args = parser.parse_args(argv)

    try:
        with HTTPDiagnosticClient() as client:
            graph = build_graph(diagnostic_client=client)
            result = graph.invoke(
                {
                    "incident": {
                        "id": args.id,
                        "service": args.service,
                        "summary": args.summary,
                    }
                },
                {
                    "configurable": {
                        "thread_id": str(uuid4()),
                        "investigation_mode": "simulated",
                    }
                },
            )
    except ValueError as exc:
        parser.error(str(exc))

    report = {
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
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
