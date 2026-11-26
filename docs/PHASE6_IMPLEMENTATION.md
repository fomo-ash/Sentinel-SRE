# Phase 6 local operator guide

Phase 6 is a simulated incident workflow. It reads fixed local observations, proposes one allowlisted synthetic action, interrupts for a human decision, and can change only the loopback mock service's in-memory health overlay. It does not rotate credentials or contact real infrastructure. The operator identifier is self-reported; authenticated identity and durable checkpoints are Phase 7 work.

## Run the local service and operator flow

From the repository root, start the Go service in one terminal:

```powershell
cd services/mock-service
go run ./cmd/server
```

In a second **interactive terminal**, start a new investigation:

```powershell
cd python
python -m pip install -e .
python -m sentinel.cli --id diag-db-auth --service api --summary "Investigate synthetic API database login rejection" --simulated-remediation
```

The CLI prints the complete proposal, its evidence IDs and fingerprint, the new thread ID, and a checkpoint summary showing no approval or receipt. It waits for `approve` or `reject` and then a nonblank operator ID. Entering anything else, closing input, or declining to supply an operator ID never records an approval. The command has no approval flag, environment shortcut, or canned decision. It refuses redirected input because remediation review requires an interactive terminal. The default CLI command without `--simulated-remediation` remains read-only.

The CLI resumes `Command(resume=...)` with the same `thread_id`, proposal ID, fingerprint, incident, service, decision, and operator ID. The graph supplies the decision ID and time and checkpoints the decision before its separate action node can run. A rejected decision ends with a report and no action. An approved decision in this mode can send one local action request, look up the receipt by the stable action ID, read incident health again, and report the verification outcome. Keep the CLI process open until the decision and report are complete: `InMemorySaver` cannot resume a pending thread after process restart.

## Local action and verification contract

Only `diag-db-auth` for `api` can propose `restore_simulated_db_login`. The proposal needs a supported diagnosis citing the current `database_auth` health and `db-auth-rejected` log observations. Other fixtures, incomplete reads, and inconclusive diagnoses produce `no_action`.

The Go service accepts only `POST /incidents/diag-db-auth/remediations` with the recorded approval and the fixed action. It atomically records an `applied` receipt and overlays that incident's health with a `db_login_restored` check. Identical action ID replay returns the original receipt; changed content or reused approval conflicts. `GET /incidents/{id}/remediations/{action_id}` is a read-only lookup. The action ledger and overlay reset when the Go process restarts.

The graph calls `GET /incidents/diag-db-auth/health` again after an applied receipt. It reports `verified` only when the restored check and overall health are healthy and the observation time is at or after the receipt and later than pre-action health. Stale or unhealthy observations are `verification_failed`. Missing restored evidence or read/response failures are `verification_unknown`. The post-action result remains separate from the pre-action evidence. Process `GET /health` does not verify an incident.

| Report outcome | Meaning |
| --- | --- |
| `no_action` / `proposal_ready` | Policy abstained, or a proposal was shown without approval or execution. |
| `rejected` / `approved` | Approval-only decision ended without execution; rejection in simulated mode also ends without execution. |
| `execution_failed` / `execution_unknown` | The action failed definitively or its effect could not be established. Do not use a new action ID to retry an unknown result. |
| `verification_failed` / `verification_unknown` | An action receipt exists, but a fresh health read did not verify recovery. |
| `verified` | The applied action and a fresh matching healthy observation are both recorded. |

The report carries the unchanged diagnosis and final severity, proposal and decision records, execution receipt, verification result, and follow-up. An applied receipt alone is never reported as recovery.

## Inspect and verify

In the CLI's printed pending checkpoint, `approval_recorded` and `action_receipt_recorded` are both `false`. The terminal report includes its `thread_id` and exact outcome. Programmatic callers can inspect `graph.get_state(config).values` and `graph.get_state_history(config)` while the same graph instance is alive. Do not print environment variables or credentials in a review record.

Run the automated contracts from `python/` and `services/mock-service/`:

```powershell
python -m pytest -q tests
ruff check src tests evaluation retrieval
ruff format --check src tests evaluation retrieval
go test -count=1 ./...
```

The Python tests use fake clients and HTTP transports, including approve, reject, replay, and failed-verification paths. A live approval must be made by a human inspecting the exact pending proposal. See [PHASE6_REVIEW.md](../python/evaluation/PHASE6_REVIEW.md) for the recorded checks and remaining acceptance status.
