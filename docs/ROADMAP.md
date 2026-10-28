# LangGraph implementation phases

Build the graph first, using deterministic fixtures and fake clients. Add external integrations only after transitions and safety properties are tested. Each phase should be a small, reviewable change.

| Phase | Write code in | Scope | Done when |
| --- | --- | --- | --- |
| 0. Foundation (done) | `python/src/sentinel/{schemas,graph}.py` | Typed incident state, intake node, in-memory checkpoint | Intake and checkpoint tests pass |
| 1. Classification | `schemas.py`, `nodes/classify.py`, `graph.py` | Severity, symptoms, input validation, deterministic classification policy | Tests cover each severity and invalid reports |
| 2. Hypotheses | `schemas.py`, `nodes/hypothesize.py`, `graph.py` | Structured hypotheses with rationale; bound their count | Graph preserves incident facts and produces testable hypotheses |
| 3. Diagnostic loop | `tools/diagnostics.py`, `nodes/investigate.py`, `nodes/evaluate.py`, `graph.py` | Fake logs, metrics, health; conditional edges to gather more evidence or stop; maximum investigation rounds | Tests cover both routes, tool failure, and loop limit |
| 4. Diagnosis | `nodes/diagnose.py`, `schemas.py`, `graph.py` | Evidence-linked diagnosis, confidence, unresolved questions; insufficient-evidence outcome | No unsupported diagnosis is reported as fact |
| 5. Human approval | `nodes/approval.py`, `graph.py`, `schemas.py` | `interrupt()`, thread IDs, resume via `Command`, explicit approve/reject decision | Tests prove no remediation occurs before approval or after rejection |
| 6. Remediation and verification | `tools/remediation.py`, `nodes/remediate.py`, `nodes/verify.py`, `graph.py` | Allowlisted simulated action, idempotency key, post-action health check, incident report | Tests cover success, failure, retry, and unhealthy verification |
| 7. Model integration | `nodes/` and `config.py` | Inject provider-neutral model interface into classification, hypotheses, and diagnosis; structured output with validation | Fake-model tests pass without API keys; provider errors are bounded |
| 8. Durability and operations | `graph.py`, `config.py`, tests | Persistent checkpointer, run correlation, telemetry, timeout/retry policy, recovery tests | Interrupted runs survive process restart and can resume safely |
| 9. Service and MCP | `services/mock-service/`, `python/src/sentinel/tools/` | Simulated incidents and diagnostics over Go HTTP; later an MCP server/client | Contract tests and end-to-end simulated incident pass |

## Graph code map

- `python/src/sentinel/schemas.py`: `IncidentReport`, typed graph state, evidence, hypotheses, approval and action records. Keep state serializable and separate observations from conclusions.
- `python/src/sentinel/graph.py`: `StateGraph`, node registration, edges, conditional routers, cycle limits, and checkpointer compilation. Keep business logic in nodes.
- `python/src/sentinel/nodes/`: one focused node per decision or effect. Return partial state updates; avoid mutating the incoming state.
- `python/src/sentinel/tools/`: interfaces and clients for diagnostic reads and simulated remediation. Keep side effects behind these interfaces.
- `python/tests/`: graph transition, checkpoint/resume, failure, and safety tests. Use fake tools and models for deterministic coverage.

## Production release gates

The current repository is a foundation. A production deployment requires durable checkpoint storage, authentication and authorization for approval, strict remediation allowlists, idempotent actions, bounded loops, timeouts and retries, observability, incident data retention rules, and end-to-end recovery tests. Keep all remediation simulated until these controls are implemented and reviewed.
