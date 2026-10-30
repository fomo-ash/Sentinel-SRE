# Baseline review — incidents v1

The fixed 24-case synthetic dataset was reviewed against the deterministic classifier on 2026-10-30. Its SHA-256 is `814df950f3a748f3bab7259e7bb84ae7f9207ac1e418cfe878a2b96d232f12dd`. Keep this version and its labels unchanged after recording results. Add a new dataset version if a label must change, and report results for both versions.

Run from `python/`: `python evaluation/evaluate_baseline.py`.

| Measure | Result |
| --- | ---: |
| Severity accuracy | 19/24 = 79.2% |
| Symptom precision (micro) | 100% |
| Symptom recall (micro) | 75% |
| Cases with any mismatch | 9/24 |

The reference labels remain reasonable under the [label policy](README.md). The baseline missed paraphrases and impact wording outside its phrase lists; this review does not justify changing labels to match the implementation.

| Case | Reference vs baseline | Review |
| --- | --- | --- |
| `eval-001` | `sev1` correct; missed `elevated_errors` | HTTP 503 responses are errors, but the symptom matcher only names `http 500` and a few phrases. |
| `eval-002` | `sev1` vs `unknown`; missed `high_latency` | “Every payment attempt” states total impact, and “gateway timeout” is a timeout symptom. Neither wording matches current patterns. |
| `eval-005` | `sev1` vs `unknown`; `network_connectivity` correct | “Every customer” states widespread impact without using a configured severity phrase. |
| `eval-008` | `sev2` vs `unknown`; missed `high_latency` | One in five queries timing out establishes partial degradation; “times out” differs from the configured timeout phrases. |
| `eval-009` | `sev2` correct; missed `elevated_errors` | “Intermittent failures” is recognized as degraded impact, but is absent from symptom patterns. |
| `eval-011` | `sev2` correct; missed `elevated_errors` | The report says “requests are failing”; the symptom pattern is “requests failing”. Database symptom was found. |
| `eval-012` | `sev2` vs `unknown`; `network_connectivity` correct | One availability zone is affected while others work; the matcher has no scoped-impact rule. |
| `eval-016` | `sev3` vs `unknown`; symptoms correct | “Only one test account” is clearly isolated but does not match “one user” or “single request”. |
| `eval-017` | `sev3` correct; missed `high_latency` | “Timed out” differs from the configured timeout phrases. |

Five cases have a severity mismatch (`002`, `005`, `008`, `012`, `016`). Six have a missed symptom (`001`, `002`, `008`, `009`, `011`, `017`). The nine-case count is the union of severity and symptom errors, not their sum.

These are failure modes of a deliberately small keyword baseline, not evidence that a future LLM improves results. Use the same hidden reference labels for LLM-only and LLM-with-retrieval evaluations, and create a separate held-out set before making generalization claims.
