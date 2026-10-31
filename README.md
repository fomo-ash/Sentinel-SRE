# Sentinel

Sentinel is an AI SRE agent for **simulated** incidents, designed with production-grade engineering practices. The current foundation has typed incident state, input validation, a deterministic classification baseline, in-memory checkpoints, and a local Go health service. Investigation and remediation are planned work; the current code is not production ready.

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

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. Install the optional LLM integration later with `pip install -e '.[llm,dev]'`; no provider key is needed now.

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

See the [current status and file checklist](docs/STATUS.md), [LangGraph implementation phases](docs/ROADMAP.md), and [architecture](docs/ARCHITECTURE.md).

