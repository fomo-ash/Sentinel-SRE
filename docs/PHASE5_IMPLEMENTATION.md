# Phase 5: grounded diagnosis and comparison

This guide describes the implemented simulated diagnosis path and how to run the Phase 5 comparison. All incidents, diagnostics, and historical cases are synthetic. The default graph remains local and deterministic. Neither diagnosis route authorizes or executes remediation.

## Flow and ownership

```text
incident -> optional history -> classification -> severity policy
    -> opt-in simulated health, metrics, logs loop -> finish_investigation
    -> deterministic diagnosis (default) OR model diagnosis (explicit)
    -> END
```

`python/src/sentinel/graph.py` owns routing and `InMemorySaver` checkpoints. `nodes/diagnose.py` owns diagnosis validation, limited model retries, one repair, policy checks, and fallback. `openai_diagnoser.py` owns one optional provider call at a time and the grounded system prompt. `schemas.py` owns the state and output contracts. The Go service and `HTTPDiagnosticClient` supply only read-only local synthetic observations. The evaluator can read the same embedded Go snapshots directly without starting an HTTP server.

The graph's default route ends after severity policy. `investigation_mode="simulated"` requires a diagnostic client and a synthetic `diag-` incident ID. An investigated run defaults to `diagnosis_mode="deterministic"`. `diagnosis_mode="llm"` additionally requires an injected diagnosis model and is rejected before any diagnostic read if that model is missing. Classification has its own independent `classification_mode`; selecting model diagnosis does not automatically select model classification. History is also independent and opt-in through `retrieval_mode="history"` with an injected retriever.

## Model input and output boundary

The model node serializes only `incident`, `current_evidence`, `diagnostic_failures`, `investigation_stop_reason`, and `historical_context`. Current evidence is filtered to the requested incident ID and service. Historical context contains labeled past synthetic cases and similarity scores, but it cannot supply current citations. The evaluator's expected severity, cause/status, evidence IDs, and reviewer notes never enter the graph or provider request.

The provider adapter requests a `ModelDiagnosis` with `status`, `summary`, `hypotheses`, `unresolved_questions`, and `historical_context_ids`. The model cannot assign `source` or severity. The node sets `source="llm"` only after validation, and records `diagnosis_source` separately from classification source. `validate_diagnosis` checks the structure, current-incident citation IDs, and historical ID separation. The node additionally requires an inconclusive result to have no hypotheses and limits the size of stored text and lists.

**Supported findings are deliberately narrow.** The current policy accepts only the explicit API database login rejection shown by the original `diag-db-auth` health and log observations. A model must identify login/authentication rejection and cite both validated current items. The node replaces model wording with the approved narrow mechanism statement; it does not allow the model to assert why the login was rejected. A plausible cause with unrelated citations, a history citation, symptoms alone, or contradictory or missing observations cannot become a supported result. This policy is intentionally limited; the alternate `diag-db-auth-alt` challenge fixture tests that boundary. Adding another supported mechanism requires an explicit policy change and new tests, rather than relying on the prompt alone.

For the model route, each provider request gets at most three attempts. Only timeout, connection failure, rate limiting, and temporary provider unavailability are retried, with bounded backoff. An invalid schema, citation, or policy result gets one grounded repair request that includes the original input and validation errors. Repair has the same per-request transient retry limit. A permanent provider error, exhausted retry, or failed repair yields a validated `inconclusive` result with `diagnosis_source="fallback"` and a fixed `diagnosis_failure_reason`. Even if the deterministic rule could support a mechanism, a failed model route does not silently claim that cause. State records actual `diagnosis_model_calls`, whether repair was attempted, and diagnosis-node latency. `final_severity` is never changed by diagnosis.

## Run an investigated incident

Install from `python/` with `python -m pip install -e '.[llm,dev]'`. Start the loopback Go service from `services/mock-service/` with `go run ./cmd/server`. Set `OPENAI_API_KEY` and `SENTINEL_OPENAI_MODEL` only in the local process environment or an ignored local `.env` loaded explicitly by your own caller. Python does not automatically load `.env`. The model name must support the structured output method used by the adapter.

```python
from sentinel.graph import build_graph
from sentinel.openai_diagnoser import create_openai_diagnoser
from sentinel.tools.diagnostics import HTTPDiagnosticClient

with HTTPDiagnosticClient() as client:
    graph = build_graph(
        diagnostic_client=client,
        diagnosis_model=create_openai_diagnoser(),
    )
    state = graph.invoke(
        {"incident": {
            "id": "diag-db-auth",
            "service": "api",
            "summary": "API database connections fail; impact is unconfirmed.",
        }},
        {"configurable": {
            "thread_id": "unique-synthetic-run",
            "investigation_mode": "simulated",
            "diagnosis_mode": "llm",
        }},
    )
    print(state["diagnosis"], state["diagnosis_failure_reason"])
```

This example uses deterministic classification and model diagnosis. To compare model classification too, inject `create_openai_classifier()` as the first `build_graph` argument and set `classification_mode="llm"`. For history, install the `retrieval` extra, inject `HistoricalIncidentRetriever()`, and set `retrieval_mode="history"`. The retrieval model may need a first-time download; no historical result becomes current evidence.

## Reproduce the three-arm comparison

`python/evaluation/investigation_dev_v1.jsonl` and `investigation_holdout_v1.jsonl` contain project-authored labels outside the Go fixture store. The development cases cover the original supported mechanism, symptom-only degradation, and missing data. The holdout cases use a separate alternate authentication fixture, a worker symptom case, and a healthy snapshot. These are small synthetic sets, and no independent label reviewer was available; results are exploratory. The alternate fixture expresses the same SQLSTATE with different IDs and wording to expose overly narrow policy matching.

The evaluator runs three arms with the same incident input, embedded snapshot, diagnostic order, timeout/budgets, and fresh checkpoint thread per case and arm:

| Arm | Classification | History | Diagnosis |
| --- | --- | --- | --- |
| `baseline` | Deterministic | Off | Deterministic |
| `model` | OpenAI | Off | OpenAI with policy validation |
| `model_history` | Same OpenAI model | Local top-k retrieval | Same OpenAI diagnoser with labeled past context |

From `python/`, install `python -m pip install -e '.[llm,retrieval,dev]'`. A live run requires explicit `--live`; use an ignored local env file if needed. Run the development set before the held-out set. Use different output files and keep the datasets unchanged after recording results:

```powershell
python evaluation/evaluate_investigation.py --live --env-file ../.env `
  --dataset evaluation/investigation_dev_v1.jsonl `
  --output evaluation/results_phase5_dev_v1.json
python evaluation/evaluate_investigation.py --live --env-file ../.env `
  --dataset evaluation/investigation_holdout_v1.jsonl `
  --output evaluation/results_phase5_holdout_v1.json
```

Pass `--retrieval-model <local-model-directory>` to use verified cached weights. The evaluator records dataset and Go fixture SHA-256 hashes, model IDs, per-case results, and per-arm metrics. It reports severity and symptom quality, diagnosis status and supported-case recall, correct abstention, citation validity, unsupported claims, fallback and repair rates, diagnostic/model calls, mean and p95 total latency, and provider-reported token counts. Failed calls may not report usage. The separate Phase 3 retrieval review remains the source for fixed-query Recall@k and MRR@k; the small Phase 5 cases have no independent history-usefulness rubric. No token prices or cost estimates are inferred.

Run `pytest -q tests --basetemp=.pytest_phase5_tmp`, `ruff check src tests evaluation retrieval`, and `ruff format --check src tests evaluation retrieval` from `python/`; run `go test -count=1 ./...` from `services/mock-service/`. The explicit pytest temp path is useful on locked-down Windows profiles. The [Phase 5 review](../python/evaluation/PHASE5_REVIEW.md) records measured results and remaining limits.

## Remaining system limits

These checks validate a small synthetic workflow. They do not establish real incident diagnosis quality, production latency, or generalization to unseen causes. The process-local checkpointer does not survive restart. There is no remediation node; Phase 6 requires a checkpointed human approval before any simulated action. Phase 7 addresses durable recovery, authorization, telemetry, retention, and MCP contracts. No production infrastructure credentials or direct infrastructure actions are part of this phase.
