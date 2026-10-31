# Sentinel status and file-by-file work list

Updated 2026-10-31. This tracks implemented code, planned work, and the next files to edit. The current application is a simulated-incident foundation, not a production-ready SRE agent. See [ROADMAP.md](ROADMAP.md) for learning objectives and acceptance criteria.

## Done

- [x] **Foundation:** `python/src/sentinel/schemas.py` defines incident and graph state; `python/src/sentinel/graph.py` validates input and checkpoints the graph in memory. `python/tests/test_schemas.py` and `python/tests/test_graph.py` cover the boundary.
- [x] **Deterministic baseline:** `python/src/sentinel/nodes/classify.py` extracts known symptoms and assigns conservative severity. `python/tests/test_classify.py` covers impact, negation, and conflicts. It is not a production classifier.
- [x] **Fixed evaluation set:** `python/evaluation/incidents_v1.jsonl` has 24 labeled synthetic cases. `evaluate_baseline.py`, `BASELINE_REVIEW.md`, and the evaluation tests record 19/24 severity accuracy, 100% symptom precision, and 75% symptom recall on this set. The labels are frozen for comparisons.
- [x] **Model boundary:** `python/src/sentinel/schemas.py` has a separate structured classifier output contract. `python/src/sentinel/nodes/llm_classify.py` accepts an injected model, validates output, and makes one repair attempt if the structure is invalid. `python/tests/test_llm_classify.py` tests success, invalid output, repair, and timeout propagation. No real model is connected.
- [x] **Severity policy in isolation:** `python/src/sentinel/nodes/severity_policy.py` retains supported baseline severity and can map exact incident-summary references when the baseline is unknown. `python/tests/test_severity_policy.py` covers supported, unsupported, absent, and conflicting impact. The policy is not yet wired into LangGraph.
- [x] **Local Go service:** `services/mock-service/cmd/server/main.go` and `internal/api/health.go` expose `/health` on loopback; `internal/api/health_test.go` contains a handler test. Go tests have not been run in this environment because Go is not installed.

## Next: finish LLM classification (Phase 2)

Work through these in order; keep the deterministic path available throughout.

1. [x] **Define failure results and state fields.** `python/src/sentinel/schemas.py` separates classification source, failure reason, model interpretation, and final policy severity. Contract tests in `python/tests/test_schemas.py` reject invalid values and raw provider objects.
2. [x] **Add bounded failure handling.** `python/src/sentinel/nodes/llm_classify.py` retries transient calls at most three times with backoff. Invalid repair or exhausted retry records `baseline_fallback` and a reason, using `nodes/classify.py`. Fake-model tests cover invalid output, timeout, unavailability, rate limits, recovery, and permanent failure.
3. [x] **Enforce severity policy separately.** `python/src/sentinel/nodes/severity_policy.py` uses baseline severity or exact report references, and returns `unknown` for unresolved impact. `python/tests/test_severity_policy.py` covers unsupported and conflicting claims. Graph wiring remains in item 5; a model recommendation does not authorize remediation.
4. [ ] **Create a provider adapter and prompt.** Add a focused adapter under `python/src/sentinel/` and an optional provider dependency in `python/pyproject.toml` only when choosing a provider. Keep the fake model in tests. Provide the schema and grounding instructions to the real model; do not include evaluation labels in prompts. Document required environment variables with placeholders in `.env.example` and setup in `README.md`.
5. [ ] **Wire the optional route into LangGraph.** Edit `python/src/sentinel/graph.py` to select deterministic or model classification through an explicit configuration or conditional edge. Keep a bounded path to `END`; test both routes, checkpointed output, fallback, and exception handling in `python/tests/test_graph.py`. Update `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` when routing changes.
6. [ ] **Compare and release carefully.** Extend `python/evaluation/evaluate_baseline.py` or add a sibling evaluator for LLM-only results. Compare severity accuracy, symptom precision/recall, structured-output validity, fallback rate, latency, and cost on the frozen v1 set. Add a separate held-out set before claiming generalization. Keep the baseline as default until results and failure paths are reviewed.

## Later phases and primary files

| Phase | Main files to edit or add | Deliverable |
| --- | --- | --- |
| 3. Historical retrieval | `python/evaluation/` for separate synthetic history; `python/src/sentinel/tools/retrieval.py`; `python/src/sentinel/nodes/retrieve.py`; `python/tests/test_retrieval.py` | Local embeddings, top-k scores, provenance; history remains distinct from current evidence |
| 4. Diagnostics and loop | `services/mock-service/internal/api/`, `services/mock-service/internal/incidents/`; `python/src/sentinel/tools/diagnostics.py`; `python/src/sentinel/nodes/investigate.py`, `evaluate.py`; `graph.py`; tests | Simulated logs and metrics, bounded conditional investigation loop, tool failure paths |
| 5. Diagnosis and evaluation | `python/src/sentinel/nodes/diagnose.py`; `schemas.py`; `python/evaluation/`; tests | Evidence-linked diagnosis and measured comparison of baseline, LLM, and retrieval |
| 6. Approval and remediation | `python/src/sentinel/nodes/approval.py`, `remediate.py`, `verify.py`; `tools/remediation.py`; `graph.py`; Go mock endpoints; tests | Checkpointed human interrupt before allowlisted simulated action, then verification and report |
| 7. Durability and MCP | `python/src/sentinel/config.py`, `graph.py`, `tools/`; MCP server/client package; tests; deployment docs | Persistent checkpoint recovery, telemetry, access control, MCP diagnostics contracts |

## Current verification

- As of 2026-10-31, all 40 Python tests pass; Ruff lint and formatting pass. Recheck from `python/` with `pytest -q tests`, `ruff check src tests evaluation`, and `ruff format --check src tests evaluation`.
- Go is not installed in the current environment, so `go test ./...` remains a verification task for `services/mock-service/`.
- The repository has no real LLM provider, embeddings, vector database, MCP server, remediation path, or persistent checkpointer yet.
