# Phase 4 simulated diagnostics review

Recorded 2026-11-23. Phase 4's read-only diagnostic loop passes its acceptance gate on fixed local fixtures. This review covers the diagnostic client, bounded graph transitions, and local Go integration. The downstream deterministic diagnosis node belongs to Phase 5 and is reported here only to confirm the handoff; diagnosis quality remains a separate evaluation.

## Verification

From `services/mock-service/`, `go test -count=1 ./...` passed for the API and incident packages. The server returned `{"status":"ok"}` from process `GET /health` and bound to `127.0.0.1:18080` for this run. Port 8080 was already occupied. From `python/`, `pytest -q -rs tests --basetemp .venv-win/pytest_tmp` passed with **148 passed, 1 skipped** after adding explicit `unavailable` retry and budget checks. The skip is the optional `langchain_openai` integration, which is outside Phase 4. `ruff check src tests evaluation retrieval` and `ruff format --check src tests evaluation retrieval` passed.

The Python test suite exercises the default classification-only route, successful reads, empty data, malformed and mismatched responses, 404, timeout, 5xx mapping, both transient retry categories, exhausted call and round budgets, checkpointed evidence, and separation of history and tool failures from current evidence. Go tests cover fixture validation and HTTP contracts. No Phase 4 route invokes remediation; there is no approval or action node yet.

## Local end-to-end CLI runs

The Go service ran on `127.0.0.1:18080`; `SENTINEL_MOCK_SERVICE_URL` pointed the CLI to that loopback origin. Each row is **one fresh CLI process and checkpoint thread**. All summaries were synthetic, with no provider key or external service. `Latency` is the graph's sum of diagnostic read elapsed times, excluding Python startup and classification. `Failures` lists recorded fixed categories.

| Incident / service | Calls | Rounds | Latency (ms) | Evidence | Stop reason | Failures | Phase 5 handoff |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `diag-healthy` / `api` | 3 | 3 | 24.888 | 5 | `checks_complete` | none | inconclusive |
| `diag-degraded` / `api` | 3 | 3 | 29.853 | 6 | `checks_complete` | none | inconclusive |
| `diag-db-auth` / `api` | 3 | 3 | 23.335 | 3 | `checks_complete` | none | supported |
| `diag-missing` / `worker` | 3 | 3 | 22.576 | 0 | `no_usable_data` | none | inconclusive |
| `diag-unhealthy` / `worker` | 3 | 3 | 21.797 | 4 | `checks_complete` | none | inconclusive |
| `diag-absent` / `api` (unknown ID) | 1 | 1 | 13.182 | 0 | `incident_not_found` | `not_found` | inconclusive |
| `diag-degraded` / `worker` (service mismatch) | 3 | 3 | 3.838 | 0 | `no_usable_data` | `invalid_response` × 3 | inconclusive |
| `diag-degraded` / `api` (unused port 18081) | 6 | 3 | 12061.592 | 0 | `no_usable_data` | `timeout` × 6 | inconclusive |

The unreachable-port run consumed the six-call budget through two timed-out attempts for each of three reads. It demonstrates that a failed local service produces no evidence and stops within the configured budget. The service-mismatch run shows that validated identity mismatches also produce no evidence. Actual 5xx and malformed HTTP responses were exercised with fake transport in automated tests, not through a public fault-injection endpoint.

## Reproduce and interpret

Start `go run ./cmd/server` from `services/mock-service/` with `SENTINEL_MOCK_SERVICE_PORT=18080`. From `python/`, install `.[dev]`, set `SENTINEL_MOCK_SERVICE_URL=http://127.0.0.1:18080`, and run `python -m sentinel.cli --id diag-degraded --service api --summary "Synthetic incident under investigation"`. Repeat with the incident/service pairs above. Use a distinct unused loopback port only for the unavailable-service check.

Environment: Windows amd64, Python 3.13.5, Go 1.27.1, LangGraph 1.2.24, HTTPX 0.28.1, pytest 9.2.0, Ruff 0.17.6. SHA-256 of the fixed Go fixtures: `health_v1.json` `75c8e6b6c7fc71bb2ba2777740a6a062ba4e0a7f7b0cb852e89946f8a1a61bd3`; `metrics_v1.json` `1032143b7edfb90aaa766c961ae05978215e0c4035c833b0a5da79bebb713012`; `logs_v1.json` `eab7e6d523ecd5ac8d2e7a9826c39fbccc9d88a9e109e236c8acc28e2715283c`.

These are single synthetic smoke runs, not a throughput or latency benchmark. The one skipped optional provider test and lack of a live 5xx endpoint do not weaken the local diagnostic contract tests, but this review does not establish real-infrastructure reliability, diagnosis quality, or restart safety. Checkpoints remain in memory. Phase 5 comparative diagnosis evaluation and Phase 7 persistence are still open.
