# Sentinel status and file-by-file work list

Updated 2026-10-31. This tracks implemented code, planned work, and the next files to edit. The current application is a simulated-incident foundation, not a production-ready SRE agent. See [ROADMAP.md](ROADMAP.md) for learning objectives and acceptance criteria.

## Done

- [x] **Foundation:** `python/src/sentinel/schemas.py` defines incident and graph state; `python/src/sentinel/graph.py` validates input and checkpoints the graph in memory. `python/tests/test_schemas.py` and `python/tests/test_graph.py` cover the boundary.
- [x] **Deterministic baseline:** `python/src/sentinel/nodes/classify.py` extracts known symptoms and assigns conservative severity. `python/tests/test_classify.py` covers impact, negation, and conflicts. It is not a production classifier.
- [x] **Fixed evaluation set:** `python/evaluation/incidents_v1.jsonl` has 24 labeled synthetic cases. `evaluate_baseline.py`, `BASELINE_REVIEW.md`, and the evaluation tests record 19/24 severity accuracy, 100% symptom precision, and 75% symptom recall on this set. The labels are frozen for comparisons.
- [x] **Model boundary:** `python/src/sentinel/schemas.py` has a separate structured classifier output contract. `python/src/sentinel/nodes/llm_classify.py` accepts an injected model, validates output, and makes one repair attempt if the structure is invalid. `python/tests/test_llm_classify.py` covers success and failure paths; live synthetic results are recorded separately from automated tests.
- [x] **Severity policy:** `python/src/sentinel/nodes/severity_policy.py` retains supported baseline severity and can map exact incident-summary references when the baseline is unknown. `python/tests/test_severity_policy.py` covers supported, unsupported, absent, and conflicting impact. Both graph routes now pass through this policy.
- [x] **Local Go service:** `services/mock-service/cmd/server/main.go` and `internal/api/health.go` expose `/health` on loopback; `internal/api/health_test.go` contains a handler test. Go tests have not been run in this environment because Go is not installed.

## Phase 2 — implemented and measured; model remains opt-in

The implementation checklist is complete. The deterministic path remains the default because challenge-set performance is limited; see [PHASE2_REVIEW.md](../python/evaluation/PHASE2_REVIEW.md).

1. [x] **Define failure results and state fields.** `python/src/sentinel/schemas.py` separates classification source, failure reason, model interpretation, and final policy severity. Contract tests in `python/tests/test_schemas.py` reject invalid values and raw provider objects.
2. [x] **Add bounded failure handling.** `python/src/sentinel/nodes/llm_classify.py` retries transient calls at most three times with backoff. Invalid repair or exhausted retry records `baseline_fallback` and a reason, using `nodes/classify.py`. Fake-model tests cover invalid output, timeout, unavailability, rate limits, recovery, and permanent failure.
3. [x] **Enforce severity policy separately.** `python/src/sentinel/nodes/severity_policy.py` uses baseline severity or exact report references, and returns `unknown` for unresolved impact. `python/tests/test_severity_policy.py` covers unsupported and conflicting claims. A model recommendation does not authorize remediation.
4. [x] **Create a provider adapter and prompt.** `python/src/sentinel/openai_classifier.py` wraps LangChain's OpenAI structured output with a grounded system prompt and translates temporary provider failures. The `llm` extra includes `langchain-openai`; `.env.example` and `README.md` describe optional configuration. Fake-client tests run without credentials, and live GPT-6 Luna results are recorded in the Phase 2 review.
5. [x] **Wire the optional route into LangGraph.** `python/src/sentinel/graph.py` selects deterministic or injected-model classification through `classification_mode`; both paths end at severity policy. `python/tests/test_graph.py` covers both routes, checkpointed output, fallback, and invalid configuration. The baseline remains the default.
6. [x] **Compare and release carefully.** `python/evaluation/evaluate_classification.py` compares both routes on the frozen v1 set and a separate eight-case challenge set. [PHASE2_REVIEW.md](../python/evaluation/PHASE2_REVIEW.md) records severity accuracy, symptom precision/recall, structured-output validity, fallback rate, latency, reported tokens, estimated cost, and limitations. The challenge set was used to correct a policy bug and is no longer independent held-out data. No generalization claim is made; the baseline stays default.

## Later phases and primary files

| Phase | Main files to edit or add | Deliverable |
| --- | --- | --- |
| 3. Historical retrieval | `python/evaluation/` for separate synthetic history; `python/src/sentinel/tools/retrieval.py`; `python/src/sentinel/nodes/retrieve.py`; `python/tests/test_retrieval.py` | Local embeddings, top-k scores, provenance; history remains distinct from current evidence |
| 4. Diagnostics and loop | `services/mock-service/internal/api/`, `services/mock-service/internal/incidents/`; `python/src/sentinel/tools/diagnostics.py`; `python/src/sentinel/nodes/investigate.py`, `evaluate.py`; `graph.py`; tests | Simulated logs and metrics, bounded conditional investigation loop, tool failure paths |
| 5. Diagnosis and evaluation | `python/src/sentinel/nodes/diagnose.py`; `schemas.py`; `python/evaluation/`; tests | Evidence-linked diagnosis and measured comparison of baseline, LLM, and retrieval |
| 6. Approval and remediation | `python/src/sentinel/nodes/approval.py`, `remediate.py`, `verify.py`; `tools/remediation.py`; `graph.py`; Go mock endpoints; tests | Checkpointed human interrupt before allowlisted simulated action, then verification and report |
| 7. Durability and MCP | `python/src/sentinel/config.py`, `graph.py`, `tools/`; MCP server/client package; tests; deployment docs | Persistent checkpoint recovery, telemetry, access control, MCP diagnostics contracts |

## Current verification

- As of 2026-10-31, all 61 Python tests pass; Ruff lint and formatting pass, and `pip check` reports no broken requirements. The optional LangChain package initializes locally, and GPT-6 Luna returned structured output in both live synthetic evaluations. Recheck from `python/` with `pytest -q tests`, `ruff check src tests evaluation`, and `ruff format --check src tests evaluation`.
- Go is not installed in the current environment, so `go test ./...` remains a verification task for `services/mock-service/`.
- The repository has an optional OpenAI provider adapter and recorded synthetic evaluation. It has no embeddings, vector database, MCP server, remediation path, or persistent checkpointer yet.
