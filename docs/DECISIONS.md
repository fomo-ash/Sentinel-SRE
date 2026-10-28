# Decisions

- **One-node foundation:** establishes a tested graph boundary before introducing external effects and model calls.
- **TypedDict state:** follows LangGraph's typed state pattern with minimal dependencies. Runtime input validation is explicit at intake.
- **Optional LangChain:** reserved for provider integration when model calls begin; the foundation requires no API key.
- **In-memory checkpoints:** temporary foundation choice; restart loses state. Durable checkpoints are required before production deployment.
- **Local-only Go server:** avoids exposing simulated remediation endpoints when they arrive.

