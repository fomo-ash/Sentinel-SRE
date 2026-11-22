# Phase 4 and Phase 5 implementation plan

Status: **partially implemented**, written 2026-11-02 and updated 2026-11-22. Phases 0–3 are implemented. The Phase 4 loopback reads, Python diagnostic client, and opt-in bounded graph loop now exist. Phase 4 measurement and review, Phase 5 diagnosis, and investigation evaluation remain planned. This plan turns the [roadmap](ROADMAP.md) into reviewable work and acceptance checks. If implementation changes a contract below, record the reason in [DECISIONS.md](DECISIONS.md) and update the roadmap and tests in the same change.

## Existing boundary to preserve

- `python/src/sentinel/graph.py` validates an `IncidentReport`, optionally retrieves synthetic history, classifies by deterministic rules or an injected LLM, applies deterministic severity policy, and optionally runs a bounded simulated diagnostic loop. The default path needs no network, embedding model, or API key.
- `python/src/sentinel/schemas.py` now has structured current-incident evidence, diagnostic counters, failures, and stop reason. The unused string `diagnosis` placeholder still needs a Phase 5 contract.
- The Go mock service exposes process `GET /health` and fixed synthetic incident health, metrics, and logs reads on `127.0.0.1:8080`. Process health is not evidence about a simulated incident.
- `python/history/incidents_v1.jsonl` is **past synthetic context**. `python/evaluation/incidents_v1.jsonl` and `incidents_holdout_v1.jsonl` are frozen classification inputs. Keep all three separate from new current-incident diagnostic fixtures and hidden diagnosis labels.
- The checkpoint store is process-local `InMemorySaver`. Phase 4–5 results are not restart-safe; persistent checkpoints remain Phase 7 work.

## Shared rules for both phases

1. Only read local simulated diagnostics. The Python HTTP client defaults to `http://127.0.0.1:8080`; no arbitrary URLs, credentials, production logs, or mutation endpoints enter the diagnostic tool contract.
2. A retrieved past incident may suggest a question or hypothesis. It is never a current observation, never a valid evidence citation, and never enough to set severity or claim a cause.
3. Record current evidence with stable IDs, incident ID, service, source kind, source reference, observation time, and a short bounded observation. Record tool errors separately; a timeout is not evidence that the service is down.
4. Keep the existing graph route as the default. `investigation_mode = "simulated"` explicitly enters the diagnostic loop with an injected client; omitted or `off` still ends after severity policy.
5. Phase 4 ends with evidence and an explicit stop reason. Phase 5 adds the diagnosis node after that handoff. Neither phase can call or expose remediation.
6. Build and test each adapter with fakes before local HTTP or model calls. Live provider use remains opt-in and its labels remain hidden from prompts.

## Proposed graph and state contract

```text
START -> receive -> optional retrieve_history -> classify -> optional llm_classify
      -> severity_policy -> [investigation_mode?]
          off: END
          simulated: plan_diagnostics -> [action available?]
              no: finish_investigation -> END (Phase 4)
              yes: run_diagnostic -> evaluate_evidence
                  continue: plan_diagnostics
                  stop: finish_investigation -> END (Phase 4)

Phase 5 changes only the terminal handoff:
finish_investigation -> diagnose -> END
```

The plan node chooses **one** allowlisted read per round from `health`, `metrics`, and `logs`. Use a deterministic order initially so every comparison arm sees the same diagnostic sequence. A missing or failed read can lead to the next kind, but no read is repeated except a bounded transient retry. Do not make the LLM generate URLs or tool names. The evaluation node checks remaining checks, missing data, and budgets; it does not infer a root cause.

Planned state additions in `schemas.py`:

| Field | Expected value and invariant |
| --- | --- |
| `diagnostic_plan` / `pending_check` | Structured allowlisted check names and reason; never raw executable instructions. |
| `investigation_round` | Number of planned diagnostic rounds completed; initialized to zero at intake. |
| `tool_call_count` | Number of **actual client attempts**, including retries and timeouts; initialized to zero. |
| `evidence` | Append-only, bounded list of current-incident observations with stable IDs and source references. No history records or error messages. |
| `diagnostic_failures` | Separate bounded list of check kind, fixed failure category, attempt count, and latency; no secrets or raw exception text. |
| `investigation_stop_reason` | One of `checks_complete`, `incident_not_found`, `max_rounds`, `max_tool_calls`, or `no_usable_data`. |
| `diagnosis` (Phase 5) | Structured result with `supported` or `inconclusive` status, hypotheses, evidence IDs, and unresolved questions; replaces the unused string placeholder. |
| `diagnosis_source` / `diagnosis_failure_reason` (Phase 5) | Distinguish deterministic, model, and fallback results without overwriting classification source or failure. |

Each evidence item should have `id`, `incident_id`, `service`, `kind` (`health|metric|log`), `source_ref`, `observed_at`, and `observation`. Use stable fixture item IDs to derive evidence IDs; validate that a returned response belongs to the requested incident and service. Cap stored observations and log text so checkpoints stay small.

## Phase 4 — simulated diagnostics and bounded loop

### What the user should get

Given a valid simulated incident ID and `investigation_mode = "simulated"`, the graph checks the local fixture through a fake or local HTTP client, stores cited observations, and stops with a reason after a bounded number of reads. The default graph still returns classification only. A missing incident or failed tool produces a visible diagnostic failure and no fabricated evidence. The graph does not produce a cause diagnosis yet.

Example of the **new part** of a successful Phase 4 state (illustrative IDs and values, not a fixture to implement verbatim):

```json
{
  "investigation_round": 3,
  "tool_call_count": 3,
  "evidence": [
    {
      "id": "diag-001:metric:error_rate",
      "incident_id": "diag-001",
      "service": "api",
      "kind": "metric",
      "source_ref": "GET /incidents/diag-001/metrics#metrics/error_rate",
      "observed_at": "2026-01-01T00:00:00Z",
      "observation": "Error rate was 12 percent over the fixture window."
    }
  ],
  "diagnostic_failures": [],
  "investigation_stop_reason": "checks_complete"
}
```

The existing `incident`, classification, and `final_severity` fields remain in the same state. There is no diagnosis field on the Phase 4 route.

### Local Go diagnostic contract

The fixture store is immutable during a request. Use a separate `diag-...` ID namespace for current simulated incidents. The Go fixture contains **observations only**; no expected cause, severity, or evaluation label. One fixture should have healthy readings, one should show degradation with correlated metrics and logs, one should have missing data, and one should support a clear but still simulated failure. Add more cases when Phase 5 evaluation is created.

| Read-only endpoint | 200 response | Other behavior |
| --- | --- | --- |
| `GET /health` | Existing process `{"status":"ok"}`. | Keep independent of incident snapshots. |
| `GET /incidents/{id}/health` | `incident_id`, `service`, fixed `observed_at`, `status` (`healthy|degraded|unhealthy|unknown`), and bounded `checks` with stable `id`, status, detail. | Unknown ID: 404 `incident_not_found`. |
| `GET /incidents/{id}/metrics` | `incident_id`, `service`, fixed `observed_at`, and bounded `metrics` with stable `id`, name, numeric value, unit, and time window. | Empty metrics is valid missing data, not zero. |
| `GET /incidents/{id}/logs?limit=N` | `incident_id`, `service`, fixed `observed_at`, and bounded `entries` with stable `id`, timestamp, component, level, and message. | Default limit 20, maximum 50; invalid limit: 400. Empty logs is valid missing data. |

Use JSON error bodies with stable codes, validate path IDs and query values, and do not accept caller-supplied paths or remote targets. Fixture timestamps stay fixed for repeatable tests. Fake clients and `httptest` handlers simulate timeouts and 5xx responses; do not add public fault-injection or remediation endpoints.

### Phase 4 file work

| File | Edit or add | Expected responsibility |
| --- | --- | --- |
| `services/mock-service/internal/incidents/store.go` | Add | Typed immutable fixture store; validate IDs, service, times, item IDs, bounded lists, and missing-data representation on load. |
| `services/mock-service/internal/incidents/fixtures/health_v1.json`, `metrics_v1.json`, and `logs_v1.json` | Implemented | Synthetic current-incident observations only. Keep labels and historical cases out; require matching IDs, services, and observation times. |
| `services/mock-service/internal/incidents/store_test.go` | Add | Valid fixture load, duplicate IDs, malformed record, missing data, and lookup behavior. |
| `services/mock-service/internal/api/health.go`, `metrics.go`, and `logs.go` | Implemented | Register three read-only incident routes; encode stable JSON and error codes; enforce log limit. |
| `services/mock-service/internal/api/diagnostics_test.go`, `metrics_test.go`, and `logs_test.go` | Implemented | `httptest` contract for health, metrics, logs, 400, 404, empty data, content type, and bounded output. |
| `services/mock-service/internal/api/health.go` | Edit | Keep process health; make `NewHandler` accept an injected store or a small reader interface. |
| `services/mock-service/internal/api/health_test.go` | Edit | Adapt handler construction and confirm existing `/health` behavior. |
| `services/mock-service/cmd/server/main.go` | Edit | Load local fixture store once and inject it into the loopback handler; fail startup on invalid fixtures. |
| `services/mock-service/README.md` | Edit | Document route examples, fixture IDs, error codes, and loopback-only behavior. |
| `python/src/sentinel/schemas.py` | Implemented for Phase 4 | Typed diagnostic action, evidence, failures, counters, and stop reason. |
| `python/src/sentinel/config.py` | Edit | Keep local URL default; add positive timeout and budget settings with safe defaults. |
| `python/src/sentinel/tools/diagnostics.py` | Implemented | `DiagnosticClient` protocol and HTTP adapter with explicit timeout, response validation, and fixed error categories. `httpx` is a direct dependency. The client does not retry; the graph will count and bound attempts. |
| `python/src/sentinel/nodes/investigate.py` | Implemented | Deterministically choose the next allowlisted read and count bounded client attempts; never emit a URL or action. |
| `python/src/sentinel/nodes/evaluate.py` | Implemented | Convert validated current responses to evidence, keep errors separate, and choose continue or terminal stop. |
| `python/src/sentinel/graph.py` | Implemented for Phase 4 | Opt-in route, diagnostic execution node, conditional loop, terminal handoff, injected client, and build-time budget validation. |
| `python/tests/test_diagnostics.py` | Partially implemented | Fake and HTTP-mock response validation, timeout, 404, 5xx, invalid JSON, and service mismatch are covered. Retry accounting awaits the graph loop. |
| `python/tests/test_investigation_graph.py` | Implemented | Planner order, evidence IDs, missing data, stop reasons, retries, budgets, and checkpointed graph transitions. |
| `python/tests/test_graph.py` | Existing | Default route unchanged; optional model and retrieval routes remain tested. |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/STATUS.md` | Edit | Explain actual implemented behavior and limitations when Phase 4 code lands. |

### Loop limits and failure rules

- Suggested defaults: at most **3 rounds**, **6 actual client attempts total**, **2 attempts per transient read**, and a **2-second timeout per attempt**. Validate positive values when building the graph. A retry consumes the same global call budget as any other attempt.
- Retry only a timeout or temporary 5xx once, and only if budget remains. Do not retry 400, 404, malformed JSON, schema mismatch, or service/incident mismatch. Record a fixed category such as `timeout`, `unavailable`, `not_found`, or `invalid_response`.
- `plan_diagnostics` must check remaining rounds and calls **before** selecting another read. Once a limit is reached, the graph routes to `finish_investigation` without another client call. A 404 for the incident stops immediately. Empty data can lead to another check but never becomes a false zero observation.
- Every path must terminate: successful three-check path, early missing-incident path, timeout path, and exhausted-budget path. Reusing a checkpoint thread must not reset counters within the same investigation.

### Phase 4 acceptance gate

- [ ] Go `go test ./...` passes from `services/mock-service/`; the server still binds to loopback and its process `/health` contract is unchanged.
- [ ] Python fake-client and HTTP adapter tests cover success, empty data, malformed payloads, timeout, 404, 5xx, bounded retry, and service/incident mismatch.
- [ ] Graph tests prove default classification behavior, the `continue` and `stop` edges, checkpointed evidence, no extra call after either budget, and no evidence fabricated from a failure or history.
- [ ] A local end-to-end smoke run reads fixture diagnostics through the Go service. Record call counts, latency, and failure categories.
- [ ] No diagnosis claim, approval interrupt, or remediation action is present in the Phase 4 graph.

## Phase 5 — grounded diagnosis and comparative evaluation

### What the user should get

An opt-in investigated incident ends with a structured diagnosis or an explicit `inconclusive` result. Every asserted current finding cites current evidence IDs from that same incident. The result lists unresolved questions and distinguishes historical suggestions from supporting evidence. Classification severity remains the Phase 2 policy output. A model failure returns a safe, traceable fallback; nothing executes remediation.

Example of an **inconclusive** Phase 5 result when the metric above is the only useful observation:

```json
{
  "status": "inconclusive",
  "summary": "The current evidence confirms elevated errors but does not establish a cause.",
  "hypotheses": [],
  "unresolved_questions": ["Which component emitted the errors?"],
  "historical_context_ids": [],
  "source": "deterministic"
}
```

If historical IDs are present, they appear only in `historical_context_ids`. A supported result would include one or more hypotheses with `supporting_evidence_ids` that refer to current evidence such as `diag-001:metric:error_rate`.

### Diagnosis contract and decision policy

`Diagnosis` should include `status` (`supported|inconclusive`), `summary`, `hypotheses`, `unresolved_questions`, `historical_context_ids`, and `source`. Each hypothesis has a cause statement plus `supporting_evidence_ids` and optional `contradicting_evidence_ids`. The structured output must use a schema separate from graph state, as classification already does.

- Validate every cited ID against the current state's evidence and current incident ID. Reject a history ID, a failure record, an unknown ID, or evidence from another incident. Historical IDs may appear only in `historical_context_ids`.
- A supported cause needs positive current evidence, preferably from at least two independent diagnostic kinds. If observations conflict, are missing, or only support a symptom, use `inconclusive` and explain the next read that would reduce uncertainty. Do not synthesize user counts, metrics, or log lines.
- Start with a small deterministic diagnosis baseline that can cite simple fixture observations or abstain. Add an injected model adapter with grounded prompt, runtime schema validation, one repair for invalid output, bounded transient retry, and deterministic inconclusive fallback. Record `diagnosis_source` and `diagnosis_failure_reason` separately from classification fields.
- The model may use retrieved history to suggest a plausible check or hypothesis, but a history match cannot fill `supporting_evidence_ids`. Diagnosis never modifies `final_severity` and never authorizes an action.

### Phase 5 file work

| File | Edit or add | Expected responsibility |
| --- | --- | --- |
| `python/src/sentinel/schemas.py` | Edit | Add separate diagnosis output contract, typed state result, source/failure enums, and evidence-reference validation helpers. Replace the unused string diagnosis placeholder. |
| `python/src/sentinel/nodes/diagnose.py` | Add | Deterministic baseline, injected model protocol, grounded prompt construction, schema validation, citation checking, bounded repair/retry, and inconclusive fallback. |
| `python/src/sentinel/openai_diagnoser.py` | Add if live provider comparison is run | Optional LangChain/OpenAI adapter using existing environment conventions; no provider initialization on the default route. |
| `python/src/sentinel/graph.py` | Edit | Route Phase 4's `finish_investigation` handoff to `diagnose`; leave the classification-only default unchanged. |
| `python/tests/test_diagnose.py` | Add | Supported and inconclusive results, evidence citation validation, history-as-evidence rejection, cross-incident IDs, malformed output repair, timeout, and fallback. |
| `python/tests/test_graph.py` | Edit | Check diagnosis path, checkpointed references, unchanged severity, no-diagnostics abstention, and all terminal routes. |
| `services/mock-service/internal/incidents/fixtures/` | Extend carefully | Add current observations for evaluation cases without storing diagnosis labels or expected causes in the service. |
| `python/evaluation/investigation_dev_v1.jsonl` | Add | Small implementation set with incident input, fixture ID, hidden cause/abstention labels, expected evidence IDs, and label rationale. |
| `python/evaluation/investigation_holdout_v1.jsonl` | Add | Separate frozen challenge cases, never used to tune prompts or policy. Seek independent human label review; otherwise mark results exploratory. |
| `python/evaluation/evaluate_investigation.py` | Add | Run three comparison arms on identical diagnostic fixtures with fresh thread IDs, hide labels from graph inputs, record per-case outputs and aggregate quality/cost metrics. |
| `python/tests/test_evaluate_investigation.py` | Add | Label isolation, same fixture and budget across arms, deterministic metric calculations, malformed labels, and provider-failure accounting. |
| `python/evaluation/PHASE5_REVIEW.md` and versioned result JSON | Add after measurement | Record dataset hashes, model/config versions, per-arm metrics, misses, reviewer method, and limits. |
| `python/evaluation/README.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/STATUS.md` | Edit | Document actual diagnosis contract, invocation, evaluation protocol, and remaining deployment limits. |

### Comparison protocol

Use the **same incident input, frozen Go snapshot, deterministic diagnostic plan, timeout, and call budget** in each arm. Only the model and retrieval route may differ:

| Arm | Classification | History | Diagnosis |
| --- | --- | --- | --- |
| Baseline | Deterministic | Off | Deterministic grounded baseline |
| Model | Injected LLM | Off | Injected grounded LLM |
| Model + history | Same LLM | Opt-in top-k history | Same LLM with labeled past context |

Run the development set first. Freeze prompts, policy, fixture, and labels before any held-out run. Use a fresh checkpoint thread per case and arm. Never send expected severity, cause, evidence IDs, reviewer notes, or other reference labels to the graph or provider.

Report at least:

1. Final severity accuracy and symptom precision/recall, with `unknown` counted as a class.
2. Retrieval Recall@k/MRR on the separate fixed retrieval queries; for investigation cases, report whether returned history was useful without treating it as current evidence.
3. Diagnosis cause correctness **only for cases with a stipulated single cause**, plus correct abstention on ambiguous or missing-data cases.
4. Evidence citation validity (all referenced IDs exist and belong to the current incident), unsupported-claim count, and a blinded human usefulness rubric for next checks. If no independent reviewer is available, label usefulness as exploratory instead of claiming improvement.
5. Structured-output validity, repair/fallback rates and reasons, actual diagnostic attempts, mean and p95 latency, provider-reported tokens, and optional cost using caller-supplied rates. Keep failed-call accounting visible.

Do not compare arms on different diagnostic observations. Do not reuse the Phase 2 challenge set as an independent holdout; it already informed a policy correction. Keep the existing 24-case classification fixture unchanged.

### Phase 5 acceptance gate

- [ ] Every supported finding cites current-incident evidence; history and tool failures are rejected as citations.
- [ ] Ambiguous, missing, contradictory, and tool-failure cases produce a useful `inconclusive` result rather than an invented cause.
- [ ] Tests cover model success, invalid output, one repair, exhausted repair/retry, timeout, fallback, and unchanged final severity.
- [ ] All three arms run on the same frozen diagnostic fixtures and independent checkpoint threads; labels stay hidden from model and retrieval input.
- [ ] A versioned result and review report every required metric, mismatches, failure rate, and limitations. No improvement or production-readiness claim is made without measured, reviewed evidence.
- [ ] The graph still has no path to remediation. Phase 6 will add explicit checkpointed human approval before any simulated action.

## Recommended implementation order

1. **Phase 4A — contracts and fixture:** agree on JSON shapes and evidence IDs; add Go store, endpoints, and Go tests. Keep `/health` stable.
2. **Phase 4B — Python boundary:** add typed state and a fake-client-tested HTTP adapter. Normalize responses before storing evidence.
3. **Phase 4C — graph loop:** add the opt-in route, planner, read node, evaluator, counters, stop reasons, and graph transition tests.
4. **Phase 4D — local integration:** run Go and Python tests, Ruff, and a loopback smoke run; document calls/latency/failures. Review Phase 4 separately.
5. **Phase 5A — diagnosis:** define structured schema and deterministic abstaining baseline, then model adapter and citation validation with fake tests.
6. **Phase 5B — graph and evaluation:** wire terminal diagnosis, freeze development/held-out cases, implement three-arm evaluator, run and review results.

Do not start Phase 6 remediation while Phase 4–5 failure paths, citations, and evaluation remain unverified.
