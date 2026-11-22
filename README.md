# Sentinel

Sentinel is an AI SRE agent for **simulated** incidents, designed with production-grade engineering practices. The current code has typed incident state, input validation, a deterministic classification baseline, an optional evaluated OpenAI classification route, opt-in historical retrieval, in-memory checkpoints, and a local Go service with synthetic incident health and metrics reads. The Python investigation loop and remediation are planned work; the current code is not production ready.

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

For optional historical context, install `python -m pip install -e '.[retrieval,dev]'` from `python/`. The first construction of `HistoricalIncidentRetriever()` loads the sentence-transformer model and may download it into the local model cache; later runs use the cached model. To check the real model without an API key:

```python
from retrieval.retriever import HistoricalIncidentRetriever

matches = HistoricalIncidentRetriever().retrieve(
    "scheduled report exports remain queued",
    top_k=3,
)
for match in matches:
    print(match["id"], match["score"], match["historical_incident"]["summary"])
```

Review whether the returned synthetic cases are relevant. This is a manual smoke check, not a ranking-quality measurement. The test suite uses fake embeddings and does not download model weights.

The [Phase 3 retrieval review](python/evaluation/PHASE3_REVIEW.md) records a 21-query local-model run, its one miss, and its limits.

To pass past cases to the optional model route, install both extras with `python -m pip install -e '.[llm,retrieval,dev]'` and inject both components:

```python
from retrieval.retriever import HistoricalIncidentRetriever
from sentinel.graph import build_graph
from sentinel.openai_classifier import create_openai_classifier

graph = build_graph(
    create_openai_classifier(),
    retriever=HistoricalIncidentRetriever(),
)
state = graph.invoke(
    {"incident": {"id": "inc-2", "service": "report-export",
                  "summary": "Scheduled report exports are delayed"}},
    {"configurable": {"thread_id": "inc-2", "classification_mode": "llm",
                      "retrieval_mode": "history"}},
)
```

`retrieval_mode` defaults to `off`. Retrieved records appear in `historical_matches`; a retrieval failure records `retrieval_failure_reason` and classification continues without history. Past cases are labeled as context in the model input and cannot set final severity or prove the current cause.

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

From `services/mock-service/`, run `make run` then `curl http://127.0.0.1:8080/health` for process health or `curl http://127.0.0.1:8080/incidents/diag-degraded/metrics` for a fixed synthetic incident snapshot. Run `make test` for Go contract tests. The service binds to loopback by default.

Copy `.env.example` to `.env` only when you need local overrides. Python reads `SENTINEL_MOCK_SERVICE_URL` from the process environment; this foundation does not load `.env` automatically.

See the [current status and file checklist](docs/STATUS.md), [LangGraph implementation phases](docs/ROADMAP.md), [architecture](docs/ARCHITECTURE.md), and [Phase 2 function guide](docs/PHASE2_IMPLEMENTATION.md). The [Phase 2 evaluation review](python/evaluation/PHASE2_REVIEW.md) records the measured results and limits.

