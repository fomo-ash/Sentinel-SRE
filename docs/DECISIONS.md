# Decisions

- **Deterministic baseline first:** a small rule set makes state transitions and failure cases testable before model calls. It is not a production classifier.
- **TypedDict state:** follows LangGraph's typed state pattern with minimal dependencies. Runtime input validation is explicit at intake.
- **Optional LangChain:** reserved for provider integration when model calls begin; the foundation requires no API key.
- **In-memory checkpoints:** temporary foundation choice; restart loses state. Durable checkpoints are required before production deployment.
- **Local-only Go server:** avoids exposing simulated remediation endpoints when they arrive.
- **Separate LLM interpretation and policy:** structured model output will report interpreted impact and uncertainty; deterministic policy will enforce severity. Model output cannot invent metrics.
- **Historical retrieval is context:** similar past incidents can guide questions but cannot prove current impact or cause. Start with a local sentence-transformer and in-memory ranking; defer pgvector until evaluation supports it.
- **Evaluation before claims:** compare baseline, LLM, and LLM plus retrieval on fixed synthetic labels, with latency and failure measurements alongside quality metrics.
- **Single graph:** conditional routing and bounded cycles are sufficient for this workflow; no multi-agent abstraction is needed.

