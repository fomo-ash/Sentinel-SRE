# Synthetic incident history

`incidents_v1.jsonl` is a separate, fictional history corpus for Phase 3 retrieval work. Each line is a JSON object with a stable `id`, `service`, `summary`, `symptoms`, `confirmed_cause`, `resolution`, `impact`, and `provenance`. The cause and resolution describe the *past* incident only; a retrieved match cannot establish the cause, impact, or severity of a current incident.

The file contains 101 invented incidents across export, catalog, checkout, payments, authentication, inventory, notifications, search, uploads, analytics, and billing. Their provenance marks them as synthetic, so they must never be presented as real postmortems. Keep this corpus separate from `python/evaluation/incidents_v1.jsonl` and `incidents_holdout_v1.jsonl`, which are labeled evaluation inputs. Copying evaluation cases into retrieval history would leak answers into future comparisons.

This corpus supplies varied context for local retrieval. `python/retrieval/history.py` loads and validates records. `python/retrieval/embeddings.py` encodes the service, summary, symptoms, and impact of each past case plus the incoming query with a local sentence-transformer model; `python/retrieval/retriever.py` ranks records in memory by cosine similarity. Confirmed causes and resolutions remain in returned context but are excluded from search text because the current cause is not yet known. Install the optional `retrieval` extra and make the model available locally before constructing the default retriever.

From the `python/` directory:

```python
from retrieval.retriever import HistoricalIncidentRetriever

retriever = HistoricalIncidentRetriever()
matches = retriever.retrieve("scheduled exports are delayed", top_k=3)
```

Each match contains `id`, a cosine `score`, and the complete `historical_incident` record, including synthetic provenance. Tied scores sort by stable ID. These are past examples, not current-incident evidence or a cause diagnosis. A first local-model result on fixed synthetic queries is in [PHASE3_REVIEW.md](../evaluation/PHASE3_REVIEW.md); its project-authored relevance labels have not been independently reviewed.
