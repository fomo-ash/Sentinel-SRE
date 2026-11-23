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

## Phase 2 model comparison

`evaluate_classification.py` compares the baseline graph route with the injected model route. It passes only `id`, `service`, and `summary` to either route; expected labels and annotator fields remain inside the evaluator. Each case and route gets a separate checkpoint thread. Results include final policy severity accuracy, micro symptom precision and recall, structured-output validity after any repair, fallback rate and reasons, mean and p95 latency, and provider-reported token usage. An optional dollar estimate uses rates supplied by the caller. It is an estimate, not a billing record: cached tokens, unreported failed calls, and provider pricing adjustments may differ.

Provider calls require the explicit `--live` flag. With an ignored local `.env` in the repository root, run from `python/`:

```sh
python evaluation/evaluate_classification.py --live --env-file ../.env \
  --dataset evaluation/incidents_v1.jsonl \
  --output evaluation/results_phase2_luna_v1.json \
  --input-usd-per-million 0.10 --output-usd-per-million 0.50
```

The example rates are for the recorded GPT-6 Luna Standard evaluation as of 2026-10-31; check the [current OpenAI pricing page](https://developers.openai.com/api/docs/pricing) for later runs. Do not put a key in this document, a result file, or a shell command. The evaluator reads only `OPENAI_API_KEY` and `SENTINEL_OPENAI_MODEL` from an explicitly supplied env file and never writes their values to results.

`incidents_holdout_v1.jsonl` is a separate eight-case synthetic challenge set with broader impact wording and two labels in each severity class. Its labels were created for this project and have not been independently reviewed, so treat it as exploratory rather than evidence of generalization. The original `incidents_v1.jsonl` remains frozen. The recorded comparisons and limits are in [PHASE2_REVIEW.md](PHASE2_REVIEW.md).

## Phase 3 historical retrieval evaluation

`retrieval_queries_v1.jsonl` contains 21 fixed, paraphrased synthetic queries. Each has one `relevant_ids` label pointing only to `python/history/incidents_v1.jsonl` and a `label_reason` explaining the match. Queries and labels are held outside the history corpus and are never supplied to the embedding model during indexing. Labels were authored for this project and have not had an independent human review; treat results as an exploratory fixture check, not evidence of production retrieval quality.

`evaluate_retrieval.py` validates the labels against history, builds one local in-memory index, and queries it once per case. It reports macro Recall@1/3/5 (the fraction of each query's relevant IDs returned), MRR@1/3/5 (reciprocal rank of the first relevant hit), per-query ranked IDs and cosine scores, failures, and latency. The result also records dataset and history hashes, model name, package version, embedding dimension, and index build time.

From `python/`, install the optional `retrieval` extra, then run:

```sh
python evaluation/evaluate_retrieval.py \
  --output evaluation/results_phase3_retrieval_v1.json
```

The first run may download model weights to the local cache. No OpenAI key is needed. Keep this query set fixed after recording results; create a new version for revised labels or broader scenarios.

The first recorded local-model result and its limitations are in [PHASE3_REVIEW.md](PHASE3_REVIEW.md). If the model host is unavailable and you already have a verified local model directory, pass `--model /path/to/model --model-id sentence-transformers/all-MiniLM-L6-v2`; the evaluator records the local `model.safetensors` hash.

## Phase 4 simulated diagnostics review

[PHASE4_REVIEW.md](PHASE4_REVIEW.md) records the Go and Python test gates, one local CLI run for each of the five fixed diagnostic fixtures, unknown-ID and failure cases, call counts, stop reasons, evidence counts, diagnostic latency, and fixture hashes. These local measurements verify the bounded read-only loop and are not a performance benchmark or a diagnosis-quality evaluation.

## Phase 5 diagnosis comparison

`investigation_dev_v1.jsonl` and `investigation_holdout_v1.jsonl` are separate, three-case, project-authored synthetic sets. They name a `diag-` fixture ID in the embedded Go snapshots and keep `expected_severity`, `expected_symptoms`, `expected_status`, `expected_mechanism`, `expected_evidence_ids`, and `label_reason` inside the evaluator. Only incident ID, service, and summary enter the graph. Neither diagnosis labels nor evidence-reference labels enter provider prompts or retrieval queries. The held-out set uses different cases and includes `diag-db-auth-alt`, whose health and log observations express the same SQLSTATE with different IDs and wording. Keep both v1 files unchanged after the recorded run; use v2 for revisions. Their labels have not had independent human review.

`evaluate_investigation.py` reads Go fixture JSON with an in-process diagnostic client, so the Go HTTP service need not run. For each case it runs `baseline` (deterministic classification and diagnosis), `model` (OpenAI classification and diagnosis), and `model_history` (the same model route with local MiniLM history). All arms use the same frozen snapshot, deterministic health/metrics/log plan, read budgets, and independent checkpoint threads. The evaluator rejects malformed labels and duplicate case IDs within a dataset and keeps reference fields out of the graph input; tests check that the two supplied datasets are disjoint. The results contain hashes of datasets and fixtures, model IDs, per-case outputs, current citation validity, unsupported final claims, severity and symptom metrics, diagnosis status and supported-case recall, abstention rate, repair/fallback reasons, diagnostic and model-call counts, mean and p95 latency, and provider-reported tokens. Failed calls may not report usage. Cost is not inferred without explicit provider rates.

Install the optional extras from `python/` with `python -m pip install -e '.[llm,retrieval,dev]'`. Set `OPENAI_API_KEY` and `SENTINEL_OPENAI_MODEL` in the local environment, or supply an ignored local `.env` file explicitly. Live calls require `--live` and use only synthetic data. Run development before holdout:

```powershell
python evaluation/evaluate_investigation.py --live --env-file ../.env `
  --dataset evaluation/investigation_dev_v1.jsonl `
  --output evaluation/results_phase5_dev_v1.json
python evaluation/evaluate_investigation.py --live --env-file ../.env `
  --dataset evaluation/investigation_holdout_v1.jsonl `
  --output evaluation/results_phase5_holdout_v1.json
```

The retrieval model may need a first-time download. Use `--retrieval-model <verified-local-directory>` for cached weights. `--live` is required even when an env file exists. The recorded results and case-level interpretation are in [PHASE5_REVIEW.md](PHASE5_REVIEW.md); [PHASE5_IMPLEMENTATION.md](../../docs/PHASE5_IMPLEMENTATION.md) explains the graph and adapter contract. The separate [Phase 3 review](PHASE3_REVIEW.md) reports fixed-query Retrieval Recall@k and MRR@k. The Phase 5 set has no independent blinded history-usefulness rubric, so it does not establish a benefit from retrieval.
