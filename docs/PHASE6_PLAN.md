# Phase 6 implementation plan: human approval and simulated remediation

Status: **Task 1 implemented; Tasks 2–8 planned**. Phase 6 must remain opt-in and local. The existing classification-only path and simulated investigation path keep their current behavior until the later Phase 6 tasks are implemented. This plan defines eight reviewable tasks in implementation order. It proposes no real infrastructure access, credentials, or automatic remediation.

## Task checklist

- [x] 1. Define the remediation state and validation contract.
- [ ] 2. Produce one deterministic, evidence-bound proposal.
- [ ] 3. Add a checkpointed human approval handoff.
- [ ] 4. Add the local mock action and idempotency ledger.
- [ ] 5. Build a narrow Python action client.
- [ ] 6. Execute only after recorded approval.
- [ ] 7. Verify the effect and produce a truthful report.
- [ ] 8. Expose the safe operator flow and record the review.

## Boundary and intended flow

```text
existing investigation -> diagnosis -> deterministic action proposal
    no eligible action -> final report -> END
    eligible action -> checkpointed human interrupt
        reject -> record rejection -> final report -> END
        approve -> record exact approval in graph state
            -> execute one synthetic action -> read incident health
            -> final report -> END
```

Only a deterministic policy can propose an action. For the first slice, allow `restore_simulated_db_login` only for the synthetic `diag-db-auth` / `api` incident when the current diagnosis is `supported` and cites the required current health and log observations. This is a **mock state change** representing restored login behavior; it is not a credential rotation or a claim about the underlying cause. The alternate fixture `diag-db-auth-alt` remains ineligible until its diagnosis policy supports it. An inconclusive diagnosis, absent or conflicting evidence, missing diagnostic data, or an unsupported incident produces no proposal. A model cannot generate an executable action name or grant approval.

Use the existing `InMemorySaver` for the checkpointed interrupt. A pending approval survives a resume in the same running process and thread; it does **not** survive process restart. Durable recovery and authenticated operators are Phase 7 work. The Go service remains bound to `127.0.0.1` and accepts only synthetic IDs. Do not reuse historical incidents or frozen evaluation labels as authorization evidence.

## Eight implementation tasks

### 1. Define the remediation state and validation contract (implemented)

- **Files:** `python/src/sentinel/schemas.py`, `python/src/sentinel/graph.py`.
- Add strict, bounded types for an action proposal, human decision, execution receipt, verification result, and final report. The proposal includes `proposal_id`, incident ID, service, allowlisted action, reason, current evidence IDs, and a stable fingerprint of the exact proposed action and evidence. The decision includes the proposal ID and fingerprint, `approve` or `reject`, a nonempty operator identifier, and a workflow-recorded decision time. Keep approval fields separate from diagnosis, severity, and input incident facts.
- Initialize every Phase 6 field at `receive_incident` so a reused checkpoint thread cannot retain a prior proposal, decision, receipt, or verification. Validate shape, lengths, allowed values, and matching incident/service at runtime; reject unknown fields and caller-supplied execution receipts.
- **Done when:** state can represent `no_action`, `awaiting_approval`, `rejected`, `approved`, `execution_failed`, `execution_unknown`, `verification_failed`, `verification_unknown`, and `verified` without interpreting missing fields as approval. No raw exception or credential enters the checkpoint.
- **Implemented:** `schemas.py` now holds bounded strict proposal, decision, receipt, verification, and report types with cross-record validators. `receive_incident` clears the new fields on every run, including a reused checkpoint thread. This contract does not propose or execute an action.

### 2. Produce one deterministic, evidence-bound proposal

- **Files:** `python/src/sentinel/nodes/propose_remediation.py`, `python/src/sentinel/graph.py`.
- Add a pure node on the opt-in Phase 6 route after either diagnosis route. Check the actual diagnosis status and required current evidence references, then choose the single action from a hard-coded allowlist. Build a stable proposal fingerprint from the incident, service, action, and sorted evidence IDs; store the human-readable rationale separately. Never read an action from model text, historical matches, or the caller's initial state.
- When policy cannot support the action, set an explicit `no_action` outcome and reason, then route directly to reporting. Keep `final_severity` unchanged.
- **Done when:** the supported `diag-db-auth` case yields one exact proposal; healthy, missing, alternate, rejected-policy, and inconclusive cases yield none. Proposal creation makes zero mutation calls.

### 3. Add a checkpointed human approval handoff

- **Files:** `python/src/sentinel/nodes/approval.py`, `python/src/sentinel/graph.py`.
- Put `interrupt(...)` in the approval node and show the complete proposal, current evidence IDs, and a plain warning that approval authorizes one synthetic action. Require the caller to resume with LangGraph `Command(resume=...)` using the **same `thread_id`**. Validate the resume payload as an explicit `approve` or `reject` decision bound to the pending proposal ID and fingerprint. A bare boolean, missing operator, stale proposal, different incident, or malformed response must never authorize execution.
- Return the validated decision as a state update from the approval node. Route to execution only from a later node after that update is checkpointed. Rejection records the decision and ends in reporting. Repeated or conflicting resume attempts must not produce a second action.
- **Done when:** the graph stops at the interrupt with no action call, rejection cannot reach execution, and an approved decision is visible in the checkpoint before the execution node runs.

### 4. Add the local mock action and idempotency ledger

- **Files:** `services/mock-service/internal/incidents/remediation.go`, `services/mock-service/internal/api/remediation.go`, `services/mock-service/internal/api/diagnostics.go`, `services/mock-service/cmd/server/main.go`.
- Add one loopback-only synthetic mutation contract for `restore_simulated_db_login` and a read contract to look up its receipt by action ID. Require a bounded action ID, incident/service, exact allowlisted action, proposal fingerprint, and recorded approval reference in the request. Reject unknown IDs, other services/actions, malformed bodies, and missing approval references with stable JSON error codes. This is a local simulation contract, not authentication of a human operator; Phase 7 must add access control before any stronger claim.
- Under a mutex, atomically store one action receipt and apply a per-process overlay to the `diag-db-auth` incident health snapshot. Give the overlay a distinct restored check ID and a post-action observation time tied to the receipt. Keep the embedded baseline fixture immutable. Repeating the same action ID and identical request returns the original receipt without reapplying; reusing the ID with different content returns a conflict. The receipt lookup allows recovery after a response is lost. No arbitrary command, URL, target, or credential is accepted.
- **Done when:** pre-action reads still expose the original fixture, one valid action changes only the intended synthetic health observation, repeated requests do not duplicate the effect, and a server restart resets this in-memory simulation.

### 5. Build a narrow Python action client

- **Files:** `python/src/sentinel/tools/remediation.py`, `python/src/sentinel/config.py`.
- Define an injected client protocol with `execute(proposal, approval, action_id)` and `get_receipt(action_id)`. Implement a loopback-only HTTP adapter that checks synthetic IDs, exact action names, response schema, incident/service identity, fingerprint, and receipt identity. Translate HTTP and transport failures into fixed categories without storing raw error strings in graph state.
- Use one HTTP mutation request per invocation. Do not automatically retry a timed-out POST. On an ambiguous result, query the receipt by the same action ID; if still unknown, stop with `execution_unknown` for human review. This preserves at-most-once intent across graph replay and lost responses.
- **Done when:** fake and local HTTP clients share the same contract, default graph construction needs no action client, and arbitrary hosts or paths cannot be selected by incident or model text.

### 6. Execute only after recorded approval

- **Files:** `python/src/sentinel/nodes/remediate.py`, `python/src/sentinel/graph.py`.
- Add an opt-in `remediation_mode="simulated"` route that requires the injected action client. Reject unknown modes or missing dependencies before entering an approval flow. At the execution node, revalidate the stored approval against the exact pending proposal and current incident, derive one stable action ID from the workflow thread and proposal, and call the client once. Preserve the returned receipt and fixed failure category in state.
- On replay, first use an existing checkpointed receipt or query the mock ledger by action ID. Never create a new action ID for the same approval. A rejected or absent decision cannot reach this node. Ambiguous or failed execution goes to reporting without claiming success.
- **Done when:** approval is a required predecessor to every client call, retry or resume cannot double-apply the action, and execution errors have explicit terminal outcomes.

### 7. Verify the effect and produce a truthful report

- **Files:** `python/src/sentinel/nodes/verify.py`, `python/src/sentinel/nodes/report.py`, `python/src/sentinel/graph.py`.
- After an applied receipt, perform a **new incident health read** through the existing diagnostic client. Check the returned incident ID and service, the action-specific restored check ID, and an observation time at or after the receipt's application time and later than the pre-action snapshot. Record the receipt, verification observation ID/time, outcome, and any fixed read failure separately from the pre-action evidence. Process `GET /health` does not count as incident verification.
- Define `verified` only when the post-action observation supports it. A stale, missing, malformed, unhealthy, or unavailable read is `verification_failed` or `verification_unknown`; it must not be reported as resolved. Produce a final report for no proposal, rejection, applied and verified, execution failure/unknown, and verification failure/unknown. Include the diagnosis and severity unchanged, proposal and approval summary if present, action receipt, and unresolved follow-up.
- **Done when:** every terminal route has one report and the report distinguishes an applied action from a verified outcome.

### 8. Expose the safe operator flow and record the review

- **Files:** `python/src/sentinel/cli.py`, `README.md`, `services/mock-service/README.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/STATUS.md`, `docs/PHASE6_IMPLEMENTATION.md`, `python/evaluation/PHASE6_REVIEW.md`.
- Add a local demonstration flow that starts an opt-in simulated investigation, prints the pending proposal, accepts an explicit approve or reject response from a human, and resumes the same thread. Do not auto-approve via a CLI flag, environment variable, model output, or canned example. Show how to inspect the checkpoint and report without displaying secrets.
- Document the local-only action and receipt contracts, process-local reset behavior, exact run commands, observed fixture IDs, and failure handling. Record an acceptance review with fixture versions, commands and outcomes, approval and rejection traces, action counts, verification results, and limits. Update status only after the behavior exists; keep Phase 6 marked planned until then.
- **Done when:** a reviewer can run the complete approve, reject, and failed-verification paths locally and can see in the record that no action occurred before an explicit human decision.

## Test files — separate from the eight implementation tasks

The files below are **tests to add or extend during implementation**. No test file is part of a task's implementation-file list above. Task 1 extended `test_schemas.py` and `test_graph.py`; the remaining files are for later tasks.

| Test file | Required coverage |
| --- | --- |
| `python/tests/test_schemas.py` | Strict proposal, approval, receipt, verification, and report validation; stale or mismatched approval. |
| `python/tests/test_graph.py` | Task 1 intake defaults and clearing of caller-supplied Phase 6 fields on a reused thread. |
| `python/tests/test_remediation_proposal.py` | Exact eligible fixture and evidence; no proposal for inconclusive, alternate, missing, healthy, conflicting, or historical-only evidence. |
| `python/tests/test_approval.py` | Interrupt payload, same-thread resume, approve/reject, malformed/bare/stale decision, decision checkpoint before execution. |
| `python/tests/test_remediation.py` | Client allowlist, loopback restriction, response validation, lost POST response and receipt lookup, no blind retry. |
| `python/tests/test_remediation_graph.py` | Full graph routes: default off, pending interrupt, rejection, approval, repeated resume/replay, execution failure/unknown, and unchanged severity. |
| `python/tests/test_verification.py` | Fresh incident read, matching receipt and observation, stale/missing/unhealthy/invalid/timeout results, and truthful reports. |
| `services/mock-service/internal/incidents/remediation_test.go` | Atomic effect and receipt, same-ID replay, conflicting reuse, unrelated fixture unchanged, baseline immutability. |
| `services/mock-service/internal/api/remediation_test.go` | Local HTTP request/response contracts, method and body validation, approval reference requirement, stable errors, receipt lookup. |

Run Python graph and client tests with fake clients and fake HTTP transports; use Go `httptest` for server contracts. Then run the full Python and Go suites, Ruff checks, and a short loopback smoke run. The acceptance gate is: **zero actions before approval or after rejection; one effect for one approved action across replay; explicit failure or unknown on lost execution or failed verification; no real infrastructure calls.**
