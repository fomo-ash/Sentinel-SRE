# Architecture

The Python package owns investigation state and LangGraph orchestration. The Go service will provide simulated incident data over local HTTP. Later, an MCP adapter will expose diagnostics to the graph. No production infrastructure is connected.

Current flow: `START -> intake -> END`. Intake validates an incident report and initializes empty investigation fields. The caller supplies a `thread_id` so `InMemorySaver` can checkpoint execution. This is a foundation, not an incident investigator yet.

`IncidentReport` records the immutable incoming facts. `InvestigationState` carries severity, symptoms, hypotheses, evidence, and diagnosis as the workflow grows. Evidence stores source and observation separately so a future diagnosis can cite what was observed.

The target graph will add classification, hypothesis generation, diagnostics, evidence review, diagnosis, human approval, remediation, verification, and reporting. `graph.py` owns topology; `nodes/` owns node behavior; `schemas.py` owns state contracts; `tools/` owns external diagnostic and remediation clients. See `ROADMAP.md` for implementation order and release gates.

