# Synthetic incident evaluation set v1

`incidents_v1.jsonl` contains 24 invented incident reports: six each labeled `sev1`, `sev2`, `sev3`, and `unknown`. The file is fixed evaluation input, not historical incidents for retrieval and not evidence of any real outage. Do not include these records in a retrieval corpus or model prompt examples when evaluating retrieval or model generalization.

Each line is one JSON object:

- `id`, `service`, `summary`: input visible to a classifier. Only these fields should be passed to a baseline or LLM classifier.
- `expected_severity`, `expected_symptoms`: reference labels for evaluation, never classifier inputs.
- `affected_components`, `reported_impact`, `uncertainty`: annotator reference fields for a future structured-output evaluator, also hidden from the classifier.

## Label policy

- `sev1`: the report explicitly states widespread or total user-facing failure.
- `sev2`: the report explicitly states meaningful partial degradation while some traffic still succeeds.
- `sev3`: the report explicitly limits the issue to one user, request, account, page, or internal probe.
- `unknown`: impact is missing, unverified, or contradicted. A component alarm alone does not establish user impact.

Symptom labels describe the reported technical observation, even if user impact is unknown. They are not root-cause labels. The current symptom vocabulary is `database_connectivity`, `network_connectivity`, `elevated_errors`, `high_latency`, and `service_outage`. An empty list means no supported symptom from that vocabulary. Component labels include only components named or directly identified in the report; they are not an inferred dependency graph. `reported_impact` is `null` when no reliable impact statement exists.

The set deliberately mixes baseline-friendly phrases with paraphrases and contradictions. Its small size, synthetic wording, and single annotator policy make it unsuitable for production performance claims. Keep this version unchanged after reporting metrics; add a new version for corrections. Before claiming model improvement, create a separate held-out set with broader wording and review its labels independently.

## Baseline evaluator

From `python/`, run `python evaluation/evaluate_baseline.py`. It passes only `{id, service, summary}` into the classifier and reports severity accuracy, micro-averaged symptom precision and recall, and mismatched IDs. Treat `unknown` as a real class, not a skipped case. The fixed results and case-by-case review are in [BASELINE_REVIEW.md](BASELINE_REVIEW.md).
