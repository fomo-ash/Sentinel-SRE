# Phase 2 classification review — GPT-6 Luna

Recorded 2026-10-31 using `gpt-6-luna` through the optional LangChain OpenAI adapter. The graph used `classification_mode = "llm"` only for the model route. The deterministic route and model route both passed through the same severity policy. No retrieval, diagnostic tools, or remediation were involved. The frozen v1 labels were not included in model inputs or prompts.

These are **single-run results on small synthetic sets**, not production performance or a generalization claim. The model route remains opt-in; the local baseline remains the default.

## Frozen `incidents_v1.jsonl` — 24 cases

The dataset is the original Phase 1 evaluation set, unchanged from [BASELINE_REVIEW.md](BASELINE_REVIEW.md). Full per-case output is in [results_phase2_luna_v1.json](results_phase2_luna_v1.json).

| Measure | Deterministic route | GPT-6 Luna route |
| --- | ---: | ---: |
| Final severity accuracy | 19/24 = 79.2% | 24/24 = 100% |
| Symptom precision, micro | 100% | 82.6% |
| Symptom recall, micro | 75.0% | 79.2% |
| Mean latency | 6.1 ms | 3,290.5 ms |
| p95 latency | 6.8 ms | 5,160.9 ms |
| Valid structured interpretation after repair | N/A | 24/24 = 100% |
| Baseline fallback | N/A | 0/24 |

All final severities matched the reference labels in this run. Six cases still had a symptom mismatch: `eval-002`, `eval-005`, `eval-008`, `eval-014`, `eval-017`, and `eval-020`. Precision fell because the model added symptoms absent from the reference labels in some cases; recall rose only slightly. This is a quality tradeoff, not an unqualified improvement.

The adapter reported 8,129 input tokens and 7,054 output tokens across 24 completed calls. Using the caller-supplied Standard rates of $0.10 per million input tokens and $0.50 per million output tokens, the evaluator estimated **$0.00434** for this run. The rate source is the [OpenAI API pricing page](https://developers.openai.com/api/docs/pricing). The estimate treats all reported input tokens as uncached and is not a billing record.

## Separate `incidents_holdout_v1.jsonl` challenge — 8 cases

This set has two cases per severity class and wording outside the baseline phrase list. Full per-case output is in [results_phase2_luna_holdout_v1.json](results_phase2_luna_holdout_v1.json).

| Measure | Deterministic route | GPT-6 Luna route |
| --- | ---: | ---: |
| Final severity accuracy | 2/8 = 25.0% | 3/8 = 37.5% |
| Symptom precision, micro | 100% | 66.7% |
| Symptom recall, micro | 40.0% | 80.0% |
| Mean latency | 9.1 ms | 3,443.2 ms |
| p95 latency | 52.3 ms | 4,998.2 ms |
| Valid structured interpretation after repair | N/A | 8/8 = 100% |
| Baseline fallback | N/A | 0/8 |

The model route returned `unknown` for five explicit-impact cases. In `holdout-001` and `holdout-002`, the model recommended `sev1`, but the fixed policy did not recognize the quoted impact wording. In `holdout-003`, the model recommended `sev3` for a report saying about 30% of attempts timed out; policy kept `unknown` rather than adopting that unsupported recommendation. `holdout-005` and `holdout-006` also remained `unknown` because the policy's isolated-impact phrase list is narrow. The contradiction in `holdout-008` now resolves to `unknown` in both routes after a policy correction.

The challenge set was used to identify that contradictory-impact bug and was therefore **not held out from development after that point**. Its labels were written for this project and have not had independent review. It cannot support a generalization claim. A new, independently reviewed set is needed for that purpose.

The adapter reported 2,707 input tokens and 2,305 output tokens across eight completed calls. At the same supplied rates, the estimated cost was **$0.00142**. The estimate excludes any billing details not present in response usage metadata.

## Release decision and limits

- Keep `classification_mode = "deterministic"` as the default. The model route is explicitly opt-in.
- Do not claim production severity accuracy. Both sets are small and synthetic, the challenge labels lack independent review, and the observed model output may vary between runs.
- Structured-output validity was measured **after** the one repair opportunity. The evaluator does not currently report first-attempt validity separately.
- Fallback and retry behavior is covered by fake-model tests, but neither live run encountered a provider failure. The observed 0% fallback rate is limited to these 32 calls.
- The policy intentionally refuses unrecognized impact phrasing rather than copying a model recommendation. This preserves uncertainty but limits recall, especially on the challenge set.
- No action can be executed from classification; remediation remains a later human-approved phase.
