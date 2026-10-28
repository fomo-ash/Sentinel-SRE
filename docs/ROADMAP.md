# Sentinel implementation roadmap

Each phase is a separate reviewable change. The current milestone ends after Phase 1. Later phases are plans, not implemented features. Use fixed synthetic fixtures and record measured results before claiming improvements.

## Phase 0 — Foundation (complete)

- **Learn:** typed `StateGraph` state, partial node updates, `START`/`END`, and process-local checkpoints. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
- **Tasks:** validate `IncidentReport`; compile the graph with `InMemorySaver`; keep the Go `/health` service local.
- **Tests / done:** invalid inputs fail; graph output and checkpoint are inspectable; Go health handler responds.
- **Dependencies:** LangGraph for orchestration; Go standard library for HTTP. **Out of scope:** durable storage and incident investigation.

## Phase 1 — Deterministic classification baseline (current)

- **Learn:** node ownership and state updates. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
- **Tasks:** `nodes/classify.py` extracts recognizable symptoms and assigns `sev1` only for explicit widespread outage, `sev2` for stated degradation, `sev3` for stated isolated impact, and `unknown` when impact is absent or ambiguous. Widespread impact takes priority over degraded or isolated impact. Ignore obvious negated phrases; treat unresolved conflicting statements conservatively. Return only `severity` and `symptoms`.
- **Tests / done:** fixed cases cover widespread, degraded, isolated, database, networking, unknown impact, negation, and conflicting statements. The graph preserves original incident facts.
- **Dependencies:** Python standard library regex only. **Out of scope:** LLM inference and a production severity policy.
- **Limitations:** keyword matching misses paraphrases and unseen patterns; local negation handling cannot reliably resolve complex grammar, contradictory reports, or temporal changes. `unknown` is preferable to invented certainty. This classifier is not production grade.

## Phase 2 — LLM classification

- **Learn:** model injection and validated structured output. [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output).
- **Tasks:** create a classifier output schema distinct from `InvestigationState`: severity recommendation, symptoms, affected components, reported impact, uncertainty or missing information, and source references where available. Use a provider-neutral model interface. Apply severity policy in a separate deterministic step. Do not invent affected-user counts or metrics.
- **Tests / done:** fake-model tests cover valid output, invalid fields, timeouts, bounded retries, unavailable provider, and fallback to `unknown` or the deterministic baseline. Record output-validity rate.
- **Dependencies:** LangChain model integration and one chosen provider package only when implemented. **Out of scope:** mandatory API keys, autonomous remediation, or replacing policy with model judgment.

## Phase 3 — Historical incidents and semantic retrieval

- **Learn:** embeddings, similarity ranking, and provenance. [Sentence Transformers semantic search](https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html).
- **Tasks:** add a small synthetic history with descriptions, symptoms, confirmed causes, resolutions, and impact. Embed history and incoming descriptions with a local sentence-transformer model. Retrieve top-k matches with scores using an in-memory index; add FAISS only if justified. Pass relevant history to the LLM classifier or investigation planner, labeled as historical context rather than current evidence.
- **Tests / done:** fixed queries have expected relevant IDs; report Recall@k and ranking quality. Similarity never directly sets severity or proves a cause.
- **Dependencies:** `sentence-transformers` when implemented; in-memory search first. **Out of scope:** PostgreSQL, pgvector, and assumptions of causal proof.

## Phase 4 — Planning and diagnostic loop

- **Learn:** conditional edges, bounded cycles, tool isolation, retries, and failure routes. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
- **Tasks:** plan diagnostic reads, query simulated logs/metrics/health via fake clients first, evaluate evidence, and route to another round or diagnosis. Set a maximum round count and tool-call budget.
- **Tests / done:** test both routes, exhausted budget, missing data, tool timeout, and retry limit. Record calls, latency, and failures.
- **Dependencies:** Go mock endpoints and an HTTP client when integrated. **Out of scope:** multi-agent orchestration and production infrastructure access.

## Phase 5 — Diagnosis and evaluation

- **Learn:** evidence-grounded conclusions and comparative evaluation. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
- **Tasks:** produce diagnosis with current-incident evidence references and unresolved questions. Create a fixed labeled synthetic evaluation set. Compare deterministic baseline, LLM without retrieval, and LLM with retrieval separately.
- **Tests / done:** measure severity accuracy, symptom precision/recall, Retrieval Recall@k and ranking quality, diagnosis usefulness from retrieved history, structured-output validity, tool-call counts, latency, and failure rates. Document datasets, labels, and results; claim no improvement without measurements.
- **Dependencies:** pytest and existing components. **Out of scope:** treating retrieved cases as current evidence.

## Phase 6 — Human approval and simulated remediation

- **Learn:** checkpoint-backed interrupts, `Command(resume=...)`, and idempotent effects. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
- **Tasks:** interrupt before any action; validate explicit approve/reject decisions; restrict actions to a simulated allowlist; verify service health and produce a report.
- **Tests / done:** no action before approval or after rejection; resume uses the same thread; retries cannot duplicate an action; failed verification is reported.
- **Dependencies:** existing LangGraph checkpointer and Go mock endpoints. **Out of scope:** real infrastructure remediation.

## Phase 7 — Durability, MCP, and release controls

- **Learn:** persistent checkpoints, recovery, and MCP tool boundaries. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture).
- **Tasks:** replace process-local checkpoints after workflow behavior is stable; add correlation IDs, telemetry, access control, retention rules, and restart/resume tests. Expose diagnostics through an MCP server/client after direct tool contracts work.
- **Tests / done:** interrupted runs survive restart; authorization and recovery tests pass; MCP contract and end-to-end simulated incident tests pass.
- **Dependencies:** persistent checkpoint backend and MCP SDK only when required. Add PostgreSQL with pgvector only if retrieval evaluation justifies persistent vector storage. **Out of scope:** unreviewed production deployment.
