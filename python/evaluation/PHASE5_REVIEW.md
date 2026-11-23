# Phase 5 review: grounded diagnosis and three-arm comparison

Recorded 2026-11-23. Phase 5's opt-in diagnosis route, failure handling, and synthetic comparison are implemented. The result is a **workflow acceptance on synthetic fixtures**, not a diagnosis-quality or production-readiness claim. The held-out authentication case was missed by all three arms. Keep deterministic classification and diagnosis as the defaults.

## Question and protocol

The comparison asks whether the same read-only diagnostic observations yield a supported, cited finding when justified, and an inconclusive result when they do not. The arms were:

| Arm | Classification | History | Diagnosis |
| --- | --- | --- | --- |
| `baseline` | Deterministic | Off | Deterministic |
| `model` | GPT-6 Luna | Off | GPT-6 Luna, validated by policy |
| `model_history` | GPT-6 Luna | Local MiniLM top-k | GPT-6 Luna with labeled past context, validated by the same policy |

The evaluator reads embedded Go fixture snapshots through the same `FixtureDiagnosticClient` for all arms. Each case and arm gets a fresh checkpoint thread. The graph receives only incident ID, service, summary, and diagnostics; reference severity, symptoms, cause/status, evidence IDs, and label rationale remain in the evaluator. Health, metrics, and logs are read in the same order, with the same timeouts and budgets. Provider calls required explicit `--live` and used only invented incidents and synthetic history. The development run completed before the held-out run; prompts, policy, fixtures, and labels were not changed between those runs.

The development set has three cases: the original API database login rejection, degradation without a proved cause, and missing observations. The held-out set has three disjoint cases: an alternate database login rejection with different evidence IDs and wording, a worker symptom case, and a healthy snapshot. The alternate case tests whether the narrow supported mechanism transfers beyond the original fixture. Labels were authored for this project and have **not** been independently reviewed. The holdout is therefore a challenge to this implementation, not a statistical estimate of generalization.

## Frozen inputs and run configuration

| Input | SHA-256 |
| --- | --- |
| `investigation_dev_v1.jsonl` | `6835b6aaf2a5de7ef7d9f7ec98315383fa9ed426144dcd6f04d411a40b57f653` |
| `investigation_holdout_v1.jsonl` | `60ee9cd6c150b048a712998512c12978cf963fc2b57d301d4bb259cf305e73eb` |
| Go `health_v1.json` | `b1d5c420e418d65d7cad8c0e7193e8c81aa6a490ea7a51d04bad5a40942850c7` |
| Go `metrics_v1.json` | `3043417d2bdec99838a6b875baab8cdab5979df3f7bf4aef78f7112cca8f5668` |
| Go `logs_v1.json` | `286c6f35cc4a2cb7d7bf1beb9ef67cb61e1fdf09e20e675fe39afc55291dc836` |

Model ID was `gpt-6-luna`; retrieval model ID was `sentence-transformers/all-MiniLM-L6-v2`, loaded from a local cache. The development result was recorded at `2026-11-23T16:41:54Z` and the held-out result at `2026-11-23T16:50:10Z`. Full per-case state summaries, provider-reported usage, hashes, and metrics are in [development results](results_phase5_dev_v1.json) and [held-out results](results_phase5_holdout_v1.json). The evaluator code and dataset loading rules are in [evaluate_investigation.py](evaluate_investigation.py).

## Measured results

Each set has one labeled supported case and two labeled abstention cases. Symptom precision and recall are micro-averaged. Status accuracy includes supported and inconclusive. Supported recall is measured over the one labeled supported case, so each change is a full 0-to-1 swing. `p95` is the nearest-rank value over only three local runs per arm; these times are descriptive samples, not a benchmark.

| Set | Arm | Severity | Symptom P/R | Diagnosis status | Supported recall | Correct abstention | Invalid citations / unsupported final claims | Repair / fallback | Mean / p95 total latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dev | Baseline | 2/3 | 0/0 | 3/3 | 1/1 | 2/2 | 0 / 0 | 0/3 / 0/3 | 88.7 / 139.9 ms |
| Dev | Model | 2/3 | 1/1 | 3/3 | 1/1 | 2/2 | 0 / 0 | 0/3 / 0/3 | 7,086.1 / 8,940.1 ms |
| Dev | Model + history | 2/3 | 1/1 | 3/3 | 1/1 | 2/2 | 0 / 0 | 0/3 / 0/3 | 7,204.3 / 8,381.6 ms |
| Holdout | Baseline | 3/3 | 0/0 | 2/3 | 0/1 | 2/2 | 0 / 0 | 0/3 / 0/3 | 14.5 / 23.3 ms |
| Holdout | Model | 3/3 | 1/1 | 2/3 | 0/1 | 2/2 | 0 / 0 | 1/3 / 1/3 | 10,163.0 / 12,773.1 ms |
| Holdout | Model + history | 3/3 | 1/1 | 2/3 | 0/1 | 2/2 | 0 / 0 | 1/3 / 1/3 | 9,975.1 / 12,611.1 ms |

All arms made exactly three diagnostic client calls per case. Model arms made one diagnosis provider call per development case; on holdout they made four across three cases because the alternate authentication case received one repair request. No classification fallback occurred. The model and model-plus-history holdout diagnosis fallback was `invalid_output` on that alternate case. The resulting diagnosis was inconclusive with no unsupported hypothesis or invalid current-evidence citation. `accepted_model_diagnosis_rate` was 3/3 on development and 2/3 on holdout for each model arm.

### Case-level review

- `p5-dev-db-auth`: all arms supported the narrow database login rejection mechanism with the current health and log evidence IDs. The question of *why* login was rejected stayed unresolved. The baseline missed the expected database-connectivity symptom in the incident summary; model arms identified it.
- `p5-dev-degraded`: all arms abstained on cause. All three missed the project severity label, which is why development severity accuracy is 2/3. This label was left unchanged after the run.
- `p5-dev-missing`: all arms returned inconclusive without invented evidence.
- `p5-holdout-db-auth-alt`: all arms returned inconclusive despite the project label of supported database authentication rejection. The deterministic rule depends on the original fixture's exact pattern. Both model arms attempted one repair and then safely fell back after output failed validation or policy. This is a substantive supported-cause recall failure, preserved as the held-out result.
- `p5-holdout-worker` and `p5-holdout-healthy`: all arms abstained correctly. Neither history nor the model converted symptoms or a healthy snapshot into a cause claim.

### Provider usage and other measures

| Set | Arm | Classifier input / output tokens (reported calls) | Diagnoser input / output tokens (reported calls) | Mean diagnosis model calls |
| --- | --- | ---: | ---: | ---: |
| Dev | Model | 1,096 / 624 (3) | 5,304 / 616 (3) | 1.00 |
| Dev | Model + history | 2,013 / 867 (3) | 6,204 / 642 (3) | 1.00 |
| Holdout | Model | 1,102 / 877 (3) | 7,784 / 1,365 (4) | 1.33 |
| Holdout | Model + history | 2,062 / 956 (3) | 9,025 / 1,165 (4) | 1.33 |

These are provider-reported counts for successful responses with usage; failed calls may not report tokens. No provider rates were supplied, so no cost estimate is recorded. The evaluator records actual diagnostic and diagnosis-model attempts separately from reported usage. Current citation validity was 100% for returned final results and unsupported final-claim count was zero; this includes safe fallbacks, so it does not measure how often raw model output was valid on its first attempt. The repair/fallback rates expose that distinction.

The separate fixed Phase 3 retrieval review measured **20/21 Recall@1 and MRR@1 = 0.9524** on 21 project-authored synthetic history queries. See [PHASE3_REVIEW.md](PHASE3_REVIEW.md) for its hashes, retrieval setup, and miss. Phase 5 did not have an independent, blinded reviewer to score whether retrieved history improved next-check suggestions. The two model arms had the same diagnosis status outcomes here; usefulness remains **unmeasured**, and no history benefit is claimed.

## Acceptance and limits

Automated fake-model tests exercise supported and inconclusive outputs, exact current-evidence citations, cross-incident and historical citation rejection, missing and contradictory evidence, one repair, exhausted repair and transient retries, timeout, permanent failure, fallback, checkpoint routing, and unchanged final severity. The evaluator tests enforce label isolation, identical fixture access across arms, fresh threads, and metric accounting. On 2026-11-23, `pytest -q tests --basetemp=.pytest_phase5_tmp` passed **179 tests**; Ruff check and format check passed over `src tests evaluation retrieval`; `go test -count=1 ./...` passed with a workspace-local Go cache. The graph has no remediation edge or action tool. The Phase 5 acceptance gate in [PHASE4_5_PLAN.md](../../docs/PHASE4_5_PLAN.md) is complete as a **bounded workflow and transparent synthetic evaluation**, with the held-out miss and reviewer limitation recorded.

The evaluation contains six synthetic cases, one supported case per set, and no independent label review or blinded usefulness review. It cannot establish real-world cause correctness, safe production behavior, provider reliability, latency distribution, or an advantage from history. The current policy deliberately accepts only one specific mechanism and is too narrow for the alternate authentication wording. A later policy change should use a new, separately versioned challenge set and require reviewed labels before any quality claim. Phase 6 must add explicit checkpointed human approval before even a simulated remediation action. Phase 7 still needs durable checkpoints, authorization, observability, retention, and deployment controls.
