# Data directory

Nothing in here is committed except this file. Every artefact below is
reproducible from the source document.

| Path | Contents | Produced by |
|---|---|---|
| `raw/` | The source PDF | `python scripts/download_document.py` |
| `processed/` | Extracted chunks and the ingestion report | `python -m app.cli ingest` |
| `figures/` | Images extracted from the PDF | `python -m app.cli ingest` |
| `renders/` | Page images, for multimodal queries and table fallback | `python -m app.cli ingest` |
| `eval_runs/` | Timestamped evaluation results | `python -m app.cli evaluate` |

## Source document

**Apple Inc. Form 10-Q for the fiscal quarter ended June 25, 2022.**
SEC accession number `0000320193-22-000070`, filed 2022-07-29.

Retrieved from the [docugami/KG-RAG-datasets](https://github.com/docugami/KG-RAG-datasets)
repository (`sec-10-q/data/v1/docs/2022 Q3 AAPL.pdf`). It is a public regulatory
filing.

The URL is configured as `DOCUMENT_URL` in `.env`, so pointing the system at a
different filing requires no code change — though the extraction logic is tuned
to the characteristics measured in [`docs/pdf-analysis.md`](../docs/pdf-analysis.md),
and a different document would warrant re-running that analysis first.
