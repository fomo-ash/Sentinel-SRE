# Mock service

Requires Go 1.22 or newer. Run `make run` and request `curl http://127.0.0.1:8080/health`. The process health endpoint returns `{"status":"ok"}`. Use `SENTINEL_MOCK_SERVICE_PORT` to change the port. The server binds to `127.0.0.1` only. Run `go test ./...` for fixture and HTTP contract tests.

The first Phase 4 diagnostic read is `GET /incidents/{id}/health`. For example, `curl http://127.0.0.1:8080/incidents/diag-degraded/health` returns a fixed synthetic observation with `incident_id`, `service`, `observed_at`, `status`, and bounded `checks` (`id`, `status`, `detail`). The fixture IDs are `diag-healthy`, `diag-degraded`, `diag-missing`, and `diag-unhealthy`. `diag-missing` returns `unknown` with an empty checks list, rather than treating missing data as healthy. Unknown IDs return 404 with `{"error":"incident_not_found"}`; invalid IDs return 400 with `{"error":"invalid_incident_id"}`. The route accepts GET only.

Fixtures contain observations only, with no cause or severity labels. Metrics, logs, diagnostic graph routing, and remediation are not implemented yet.

