# Sentinel implementation roadmap

Each phase is a separate reviewable change. Phases 0–5 are implemented; Phases 2, 3, and 5 are evaluated on small synthetic sets, and their model and retrieval routes remain opt-in. The Phase 4 diagnostic loop passed its local fixture review. The Phase 5 held-out comparison exposed a supported-cause miss, so it makes no quality or production-readiness claim. Phases 6–7 remain plans. See [STATUS.md](STATUS.md) for the current checklist, [PHASE4_5_PLAN.md](PHASE4_5_PLAN.md) for detailed Phase 4–5 work and acceptance gates, [PHASE2_IMPLEMENTATION.md](PHASE2_IMPLEMENTATION.md) for function details, and the [Phase 2](../python/evaluation/PHASE2_REVIEW.md), [Phase 3](../python/evaluation/PHASE3_REVIEW.md), [Phase 4](../python/evaluation/PHASE4_REVIEW.md), and [Phase 5](../python/evaluation/PHASE5_REVIEW.md) reviews for measured results and limits. Use fixed synthetic fixtures and record measured results before claiming improvements.

## Phase 0 — Foundation (complete)

- **Learn:** typed `StateGraph` state, partial node updates, `START`/`END`, and process-local checkpoints. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
- **Tasks:** validate `IncidentReport`; compile the graph with `InMemorySaver`; keep the Go `/health` service local.
- **Tests / done:** invalid inputs fail; graph output and checkpoint are inspectable; Go health handler responds.
- **Dependencies:** LangGraph for orchestration; Go standard library for HTTP. **Out of scope:** durable storage and incident investigation.

## Phase 1 — Deterministic classification baseline (complete)

- **Learn:** node ownership and state updates. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
- **Tasks:** `nodes/classify.py` extracts recognizable symptoms and assigns `sev1` only for explicit widespread outage, `sev2` for stated degradation, `sev3` for stated isolated impact, and `unknown` when impact is absent or ambiguous. Widespread impact takes priority over degraded or isolated impact. Ignore obvious negated phrases; treat unresolved conflicting statements conservatively. Return only `severity` and `symptoms`.
- **Tests / done:** fixed cases cover widespread, degraded, isolated, database, networking, unknown impact, negation, and conflicting statements. The graph preserves original incident facts.
- **Evaluation fixture:** `python/evaluation/incidents_v1.jsonl` provides 24 fixed, labeled synthetic reports. Keep labels hidden from classifier inputs and record baseline metrics before comparing models.
- **Recorded baseline:** `python/evaluation/BASELINE_REVIEW.md` freezes the v1 labels and documents 19/24 severity accuracy, 100% symptom precision, 75% symptom recall, and nine cases with any mismatch.
- **Dependencies:** Python standard library regex only. **Out of scope:** LLM inference and a production severity policy.
- **Limitations:** keyword matching misses paraphrases and unseen patterns; local negation handling cannot reliably resolve complex grammar, contradictory reports, or temporal changes. `unknown` is preferable to invented certainty. This classifier is not production grade.

## Phase 2 — LLM classification (implemented; opt-in)

- **Learn:** model injection and validated structured output. [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output).
- **Tasks:** create a classifier output schema distinct from `InvestigationState`: severity recommendation, symptoms, affected components, reported impact, uncertainty or missing information, and source references where available. Use a provider-neutral model interface. Apply severity policy in a separate deterministic step. Do not invent affected-user counts or metrics.
- **Implemented boundary:** `classifySchema` is separate from graph state; `interpret_incident` accepts an injected model, validates its output, and makes one repair attempt for invalid structured output. Graph state records classification source, failure reason, model interpretation, and final policy severity separately. The model node retries transient failures at most three times with backoff, then records a deterministic baseline fallback. `severity_policy.py` maps baseline impact or exact report spans to final severity. An optional OpenAI adapter supplies a grounded system prompt and structured response through LangChain. The graph uses explicit model routing, with the baseline as default. Live GPT-6 Luna comparisons on frozen v1 and a separate challenge set are recorded in [PHASE2_REVIEW.md](../python/evaluation/PHASE2_REVIEW.md); the challenge result does not support a generalization claim.
- **Implementation order:** (1) define a grounded model prompt and one provider adapter; (2) validate the structured response; (3) enforce impact-to-severity policy separately; (4) add bounded failure handling; (5) wire the optional model path into LangGraph; (6) evaluate it against the frozen dataset before enabling it by default.
- **Invalid structured output:** retry once with the original incident description, schema requirements, and validation error. Require impact claims to be traceable to the description. If the retry is invalid, use the deterministic baseline and record `classification_source = "baseline_fallback"` plus the failure reason.
- **Transient provider failure:** use a separate bounded retry with backoff for timeouts, rate limits, or temporary unavailability; then use the baseline. Never loop indefinitely. When fallback impact is ambiguous, retain `unknown` and require human review rather than inventing a severity.
- **Tests / done:** fake-model tests cover valid output, malformed output, unsupported impact claims, one repair retry, exhausted retry, timeout, unavailable provider, fallback source/reason, and `unknown` handling. Record output-validity rate, fallback rate, and latency. Classification never authorizes remediation.
- **Dependencies:** LangChain model integration and one chosen provider package only when implemented. **Out of scope:** mandatory API keys, autonomous remediation, or replacing policy with model judgment.

## Phase 3 — Historical incidents and semantic retrieval (complete on synthetic fixtures)

- **History corpus and opt-in retrieval:** `python/history/incidents_v1.jsonl` contains 101 fictional incidents with causes, resolutions, impact, and explicit synthetic provenance. `python/retrieval/history.py` validates and loads the corpus; `embeddings.py` encodes service, summary, symptoms, and impact with incoming queries; `retriever.py` ranks top-k matches by cosine score, returning full records with provenance. The graph uses an injected retriever only for `retrieval_mode = "history"`, checkpoints matches, and labels them as past context for optional model classification. Retrieval errors clear matches and allow classification to continue. The corpus is separate from frozen evaluation data. A local-model run on 21 fixed synthetic queries recorded 20/21 Recall@1 and MRR@1 of 0.9524, with one payment-versus-authentication miss; see [PHASE3_REVIEW.md](../python/evaluation/PHASE3_REVIEW.md).
- **Learn:** embeddings, similarity ranking, and provenance. [Sentence Transformers semantic search](https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html).
- **Tasks:** add a small synthetic history with descriptions, symptoms, confirmed causes, resolutions, and impact. Embed history and incoming descriptions with a local sentence-transformer model. Retrieve top-k matches with scores using an in-memory index; add FAISS only if justified. Pass relevant history to the LLM classifier or investigation planner, labeled as historical context rather than current evidence.
- **Tests / done:** fixed queries have expected relevant IDs; report Recall@k and ranking quality. Similarity never directly sets severity or proves a cause.
- **Dependencies:** `sentence-transformers` when implemented; in-memory search first. **Out of scope:** PostgreSQL, pgvector, and assumptions of causal proof.

## Phase 4 — Planning and diagnostic loop (complete on synthetic fixtures)

- **Diagnostic loop reviewed on local fixtures:** the loopback Go service exposes fixed synthetic health, metrics, and log reads. The opt-in Python graph uses an injected local client to plan health, metrics, then logs; it checkpoints current evidence, attempts, latency, failures, and a stop reason. It retries only transient read failures once, with at most three rounds and six actual client attempts. The default graph remains classification only. Fake-client transitions and local CLI runs across all five fixtures plus failure cases passed the [Phase 4 acceptance review](../python/evaluation/PHASE4_REVIEW.md). The recorded latencies are single smoke-run measurements, not performance estimates.
- **Detailed implementation plan:** [PHASE4_5_PLAN.md](PHASE4_5_PLAN.md) defines the local Go contracts, Python files, graph loop, budgets, failure rules, and verification gate.
- **Learn:** conditional edges, bounded cycles, tool isolation, retries, and failure routes. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
- **Tasks:** plan diagnostic reads, query simulated logs/metrics/health via fake clients first, evaluate evidence, and route to another round or diagnosis. Set a maximum round count and tool-call budget.
- **Tests / done:** test both routes, exhausted budget, missing data, tool timeout, and retry limit. Record calls, latency, and failures.
- **Dependencies:** Go mock endpoints and an HTTP client when integrated. **Out of scope:** multi-agent orchestration and production infrastructure access.

## Phase 5 — Diagnosis and evaluation (complete on synthetic fixtures; model opt-in)

- **Implemented boundary and result:** `schemas.py` validates a strict structured result and current-incident citations. Every opt-in simulated investigation ends at deterministic diagnosis by default; `diagnosis_mode = "llm"` selects the injected provider adapter. `nodes/diagnose.py` validates model claims against the same narrow database login rejection policy, makes at most one grounded repair, bounds transient retries, and safely returns `inconclusive` on failure. Diagnosis never changes severity. The default classification-only route leaves diagnosis empty. The three-arm live synthetic comparison on separate development and held-out sets is in [PHASE5_REVIEW.md](../python/evaluation/PHASE5_REVIEW.md): all arms missed the alternate supported authentication case, so model diagnosis remains opt-in and no improvement is claimed.
- **Detailed implementation plan:** [PHASE4_5_PLAN.md](PHASE4_5_PLAN.md) defines the diagnosis contract, grounded citation rules, three-arm comparison, datasets, and verification gate.
- **Learn:** evidence-grounded conclusions and comparative evaluation. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
- **Tasks:** produce diagnosis with current-incident evidence references and unresolved questions. Extend the existing fixed synthetic evaluation set with separate held-out cases. Compare deterministic baseline, LLM without retrieval, and LLM with retrieval separately.
- **Tests / done:** graph and fake-model tests cover citation rules, abstention, repair, retries, fallback, and unchanged severity. The evaluator records severity accuracy, symptom precision/recall, diagnosis status and supported recall, abstention, citation validity, tool and model calls, reported tokens, latency, and failure rates. Fixed-query retrieval ranking is measured separately in Phase 3. History usefulness in the Phase 5 cases is unmeasured because no independent blinded reviewer was available.
- **Dependencies:** pytest and existing components. **Out of scope:** treating retrieved cases as current evidence.

## Phase 6 — Human approval and simulated remediation

- **Detailed eight-task plan:** [PHASE6_PLAN.md](PHASE6_PLAN.md) specifies the state contract, deterministic proposal, checkpointed approval, local mock effect, idempotent client and execution, verification, operator flow, acceptance gate, and a separate test-files section. Tasks 1–7 are complete. The Task 8 interactive CLI and operator guide are implemented; [the acceptance review](../python/evaluation/PHASE6_REVIEW.md) has automated and loopback rejection evidence, with the human-operated eligible approval trace still pending.
- **Learn:** checkpoint-backed interrupts, `Command(resume=...)`, and idempotent effects. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
- **Tasks:** complete the eight tasks in the detailed plan. Start with one evidence-bound synthetic action, interrupt before execution, validate an explicit decision tied to the exact proposal, record approval in the graph checkpoint, apply at most one local effect, read incident health again, and produce a truthful report for every terminal route.
- **Tests / done:** no action before approval or after rejection; resume uses the same thread; retries cannot duplicate an action; failed verification is reported.
- **Dependencies:** existing LangGraph checkpointer and Go mock endpoints. **Out of scope:** real infrastructure remediation.

## Phase 7 — Durability, MCP, and release controls

- **Learn:** persistent checkpoints, recovery, and MCP tool boundaries. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture).
- **Tasks:** replace process-local checkpoints after workflow behavior is stable; add correlation IDs, telemetry, access control, retention rules, and restart/resume tests. Expose diagnostics through an MCP server/client after direct tool contracts work.
- **Tests / done:** interrupted runs survive restart; authorization and recovery tests pass; MCP contract and end-to-end simulated incident tests pass.
- **Dependencies:** persistent checkpoint backend and MCP SDK only when required. Add PostgreSQL with pgvector only if retrieval evaluation justifies persistent vector storage. **Out of scope:** unreviewed production deployment.
