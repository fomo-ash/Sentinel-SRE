# Phase 3 historical retrieval review

Recorded 2026-11-02. The in-memory retriever indexes 101 fictional past incidents from `python/history/incidents_v1.jsonl`. It embeds each case's service, summary, symptoms, and impact, then ranks matches for an incoming description by cosine similarity. Causes and resolutions are returned as labeled past context but are excluded from the search text. Retrieval is opt-in in LangGraph and cannot set final severity or establish the current cause.

## Fixed synthetic query result

`retrieval_queries_v1.jsonl` has 21 paraphrased queries across the history services. Each query has one project-authored relevant history ID and a written label reason. The labels have **not** been independently reviewed. The query file and history file were fixed before this model run. Per-query IDs, scores, latency, hashes, and the model version are in [results_phase3_retrieval_v1.json](results_phase3_retrieval_v1.json).

| Measure | Result |
| --- | ---: |
| Recall@1 | 20/21 (95.24%) |
| Recall@3 | 20/21 (95.24%) |
| Recall@5 | 20/21 (95.24%) |
| MRR@1, MRR@3, MRR@5 | 0.9524 each |
| Retrieval failures | 0/21 |
| Model load and 101-case index build | 8.54 s |
| Mean query latency after index build | 10.75 ms |

The only miss, `rq-007`, asks about duplicate temporary payment authorization holds after a retry. Its labeled case is `hist-024`, but the top five results were authentication incidents `hist-033`, `hist-032`, `hist-040`, `hist-039`, and `hist-034`. The wording overlaps sign-in and authorization language. This is a concrete failure to investigate with a new evaluation version; the v1 labels and results remain unchanged.

## Model and verification

- Model: `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, with `sentence-transformers==6.2.0`. Inference was local and offline.
- This workspace could not resolve the official model host. Model data was extracted from the `all-minilm-l6-v2-model==0.1.2` PyPI wheel without installing or executing that wheel. The extracted `model.safetensors` SHA-256 is `53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db`, matching the [official model file hash](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/main/model.safetensors). The model files and wheel were kept under `/tmp`, outside the repository.
- A graph smoke run used the real local retriever and a fake classifier. It retrieved `hist-001` for a delayed export report, passed labeled history into the model input, checkpointed the match, and kept final severity `unknown` despite the fake classifier recommending `sev1`.
- Automated tests use fake embeddings for deterministic ranking and failure paths. They do not require model downloads or an API key.

These 21 synthetic, single-label queries are a fixture check, not a production performance estimate. They do not test ambiguous incidents, multilingual wording, corpus growth, or independently reviewed relevance. Query latency excludes process startup and index construction, and varies by hardware and filesystem. No improvement over an alternate retriever or classifier is claimed.
