# Architecture

The Python package owns investigation state and LangGraph orchestration. The Go service will provide simulated incident data over local HTTP. Later, an MCP adapter will expose diagnostics to the graph. No production infrastructure is connected.

Current flow: `START -> receive_incident -> classify -> END`. Intake validates an incident report and initializes empty investigation fields. The classifier returns only severity and symptoms using transparent rules. The caller supplies a `thread_id` so `InMemorySaver` can checkpoint execution. This is a baseline, not an incident investigator yet.

`IncidentReport` records the immutable incoming facts. `InvestigationState` carries severity, symptoms, hypotheses, evidence, and diagnosis as the workflow grows. Evidence stores source and observation separately so a future diagnosis can cite what was observed.

Target flow: `incident input -> baseline and/or LLM classification -> historical retrieval -> investigation plan -> diagnostic tools -> evidence evaluation -> conditional loop -> diagnosis -> human approval -> simulated remediation -> verification -> report`.

`graph.py` owns topology, conditional edges, cycle bounds, and checkpoint compilation. `nodes/` owns one decision or effect per node. `schemas.py` owns graph state contracts; a future LLM classifier gets its own structured output schema, which is validated and mapped into state. `tools/` owns external clients. The Go service supplies simulated observations; an MCP adapter may expose those same diagnostic capabilities later. A historical incident match carries similarity and provenance, but is never evidence that the current incident has the same cause.

Phase 4 adds conditional edges after evidence evaluation and a bounded investigation loop. Tool and model nodes receive limited retries and timeout behavior. Phase 6 adds an interrupt before remediation and resumes only after an explicit decision. Phase 7 replaces process-local checkpointing for restart safety. No multi-agent layer is planned. See `ROADMAP.md` for acceptance criteria.

