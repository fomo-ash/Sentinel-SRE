# Decisions

- **Deterministic baseline first:** a small rule set makes state transitions and failure cases testable before model calls. It is not a production classifier.
- **TypedDict state:** follows LangGraph's typed state pattern with minimal dependencies. Runtime input validation is explicit at intake.
- **Optional LangChain:** reserved for provider integration when model calls begin; the foundation requires no API key.
- **In-memory checkpoints:** temporary foundation choice; restart loses state. Durable checkpoints are required before production deployment.
- **Local-only Go server:** avoids exposing simulated remediation endpoints when they arrive.
- **Separate LLM interpretation and policy:** structured model output will report interpreted impact and uncertainty; deterministic policy will enforce severity. Model output cannot invent metrics.
- **Validate classifier output at runtime:** retain a separate `TypedDict` output contract and use Pydantic's `TypeAdapter` to enforce types and an optional confidence range of 0–1. Declare Pydantic and typing-extensions directly because the schema uses them.
- **Inject the classifier model:** keep the model call behind a small `Protocol` so fake-model tests run without credentials. Return interpreted output separately from graph state and severity policy.
- **Bounded classification recovery:** repair invalid structured output with at most one grounded model retry; retry transient provider failures separately with backoff. After exhaustion, record the reason and use the deterministic baseline, keeping ambiguous impact `unknown` for human review. Classification is never remediation approval.
- **Historical retrieval is context:** similar past incidents can guide questions but cannot prove current impact or cause. Start with a local sentence-transformer and in-memory ranking; defer pgvector until evaluation supports it.
- **Evaluation before claims:** compare baseline, LLM, and LLM plus retrieval on fixed synthetic labels, with latency and failure measurements alongside quality metrics.
- **Freeze evaluation v1:** keep the 24-case dataset and its reference labels unchanged after the baseline review; make a new version for corrections so comparisons remain reproducible.
- **Single graph:** conditional routing and bounded cycles are sufficient for this workflow; no multi-agent abstraction is needed.

