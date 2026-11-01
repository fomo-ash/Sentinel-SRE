# Sentinel

Sentinel is an AI SRE agent for **simulated** incidents, designed with production-grade engineering practices. The current code has typed incident state, input validation, a deterministic classification baseline, an optional evaluated OpenAI classification route, in-memory checkpoints, and a local Go health service. Investigation and remediation are planned work; the current code is not production ready.

## Prerequisites

- Python 3.11+
- Go 1.22+

## Python setup

From `python/`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. The default graph needs no provider key.

For the optional OpenAI classifier, install `pip install -e '.[llm,dev]'` from `python/`, then set `OPENAI_API_KEY` and `SENTINEL_OPENAI_MODEL` in the process environment. Choose a model that supports structured output. The adapter is created only when you call `create_openai_classifier()`; the default graph still uses the deterministic baseline. `.env.example` shows placeholders, but Python does not load `.env` automatically. No real model call is made by the test suite.

To explicitly use the model path, build with `build_graph(create_openai_classifier())` and invoke it with `{"configurable": {"thread_id": "inc-1", "classification_mode": "llm"}}`. This invocation sends the incident summary to the configured provider. Without `classification_mode`, the graph uses the local deterministic path.

Try the graph:

```python
from sentinel.graph import build_graph

graph = build_graph()
state = graph.invoke(
    {"incident": {"id": "inc-1", "service": "api", "summary": "5xx spike"}},
    {"configurable": {"thread_id": "inc-1"}},
)
print(state)
```

An in-memory checkpoint lasts only while the graph process lives. Use a distinct `thread_id` per investigation.

## Go service

From `services/mock-service/`, run `make run` then `curl http://127.0.0.1:8080/health`. Run `make test` for its unit test. The service binds to loopback by default.

Copy `.env.example` to `.env` only when you need local overrides. Python reads `SENTINEL_MOCK_SERVICE_URL` from the process environment; this foundation does not load `.env` automatically.

See the [current status and file checklist](docs/STATUS.md), [LangGraph implementation phases](docs/ROADMAP.md), [architecture](docs/ARCHITECTURE.md), and [Phase 2 function guide](docs/PHASE2_IMPLEMENTATION.md). The [Phase 2 evaluation review](python/evaluation/PHASE2_REVIEW.md) records the measured results and limits.

