# Mock service

Requires Go 1.22 or newer. Run `make run` and request `curl http://127.0.0.1:8080/health`. The endpoint returns `{"status":"ok"}`. Use `SENTINEL_MOCK_SERVICE_PORT` to change the port. Run `make test` for the HTTP handler test.

The service currently has no simulated incident, metrics, logs, or remediation endpoints. The roadmap specifies when to add them.

