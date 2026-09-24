# Multimodal RAG over an SEC Form 10-Q

A retrieval-augmented question answering system over the **text**, **tables**, and
**figures** of Apple Inc.'s Form 10-Q for the quarter ended June 25, 2022.

Every answer is grounded in retrieved evidence, cites the page number printed on the
page, and declines when the filing does not support an answer.

---

## Problem statement

Answer questions about one SEC filing, covering three content types, without inventing
anything. The difficulty is not the retrieval architecture — it is that this document's
tables do not survive extraction intact.

The file is a born-digital PDF converted from EDGAR HTML, and that conversion leaves
three defects a naive RAG pipeline silently inherits:

| # | Defect | Evidence |
|---|---|---|
| 1 | **Column headers are lost.** The detected table region on page 4 begins *below* the `Three Months Ended` / `Nine Months Ended` header band. | `63,355` is extracted with no indication of which of four periods it belongs to |
| 2 | **Columns are inflated 2–3×.** `$` and `%` each occupy their own column, padded with empty spacers. | Page 4: 12 columns for 5 logical ones. Page 18: 18 for 6 |
| 3 | **Units live outside the table.** `(In millions…)` sits in the narrative above it. | Lose it and quarterly net sales read as \$82,959 instead of \$82.96 billion |

PyMuPDF's own `Table.header` does not solve the first one: measured on this document it
is **wrong on three of four sampled tables**, twice returning the first *data* row as
the header.

There is also **no figure to retrieve**. The filing contains exactly one raster image in
28 pages — the Apple logo. The figure pathway is built properly and the corpus is
reported honestly rather than quietly redefined to mean "tables".

Full measurements: [`docs/pdf-analysis.md`](docs/pdf-analysis.md).

---

## Measured results

From `python -m app.cli evaluate` over 33 questions with ground truth read from the
filing. Reproduced across two consecutive runs at identical settings.

### Retrieval

| Metric | Result | Meaning |
|---|---:|---|
| Hit rate@8 | **96.4%** | At least one page holding the answer was retrieved |
| Recall@8 | **91.1%** | Fraction of answer-bearing pages retrieved |
| Precision@8 | 34.1% | Fraction of retrieved chunks on an answer-bearing page |
| MRR | **0.724** | 1/rank of the first correct chunk |

Precision is low by construction: `top_k=8` is deliberately generous, and a question
whose answer sits on one page can retrieve at most 1–3 relevant chunks out of 8. It is
reported rather than hidden, but recall and MRR are the meaningful figures here.

### Answers

| Metric | Result |
|---|---:|
| Accuracy (23 questions with a checkable value) | **95.3%** |
| Refusal accuracy | **97.0%** |
| **Hallucination rate** | **0.0%** |
| False refusal rate | 3.6% |
| Citation accuracy | 90.1% |
| Median latency | **2.5 s** |

| Category | n | Hit@8 | Accuracy |
|---|---:|---:|---:|
| Table | 15 | 93.3% | 94.7% |
| Text | 10 | 100% | 100% (3 graded) |
| Figure | 3 | 100% | — (0 graded) |
| Unsupported | 5 | — | **100%** |

**Ingestion:** 28 pages → 135 chunks (102 text, 31 table, 2 figure) in ~25 s.
27 of 31 tables validate clean; 336 raw columns collapse to 146 (57% removed).

### What is not measured

- **Correctness of 10 narrative answers** with no single checkable value. They are scored
  on retrieval and refusal only. No LLM judge is used — see
  [`app/evaluation/runner.py`](app/evaluation/runner.py) for the reasoning.
- **Reranking.** Implemented as a deferred decision, never measured. Reported as unmeasured.

---

## Architecture

```
PDF → Loader (boilerplate strip · printed-page resolver · section tagger)
    ├─ Text   → section-aware chunking, table regions subtracted
    ├─ Table  → header recovery ABOVE bbox → column collapse → units + caption
    │           → dual representation: Markdown + structured rows
    └─ Figure → image + bbox + context + page render
    → Embeddings (text-embedding-3-small) → Qdrant  ‖  BM25
    → RRF fusion (k=60) + soft content-type routing
    → Context builder (dedupe · budget · provenance · injection fencing)
    → gpt-4o-mini  |  gpt-4o for visual questions and table fallback
    → Answer + citations + refusal flag + telemetry
```

Design documents: [`architecture.md`](docs/architecture.md) ·
[`design-decisions.md`](docs/design-decisions.md) · [`pdf-analysis.md`](docs/pdf-analysis.md)

---

## Technology stack

| Layer | Choice | Why |
|---|---|---|
| PDF | PyMuPDF | Text, fonts, images, tables through one API. Font metadata drives heading detection |
| Embeddings | `text-embedding-3-small` | Whole corpus embeds for a fraction of a cent, so re-indexing during tuning is free |
| Vector store | **Qdrant** (Docker) | Native payload filtering on provenance; exact search for deterministic evaluation |
| Lexical | `rank_bm25` | Financial queries are full of exact tokens — `Note 5`, `June 25, 2022`, `82,959` |
| Fusion | RRF, k=60 | Cosine and BM25 scores are not comparable; RRF needs no per-corpus tuning |
| Generation | `gpt-4o-mini` | Frequent path. Extraction does the hard work, so the model performs lookup |
| Vision | `gpt-4o` | Rare path — figure questions and failed-table fallback — where errors are costly |
| API | FastAPI | Typed schemas, OpenAPI docs, shared service layer with the CLI |
| Frontend | HTML/CSS/ES modules | No build step, no Node in the image |

Rejected alternatives and the reasoning are in
[`design-decisions.md`](docs/design-decisions.md) (D1–D11).

---

## Installation

Requires **Python 3.11+** and **Docker** (for Qdrant).

```bash
git clone https://github.com/Gopinath14141/SmartData_Solutions_RAG_Assessment.git
cd SmartData_Solutions_RAG_Assessment

python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate         # macOS / Linux

pip install -r requirements.txt
```

### Environment variables

```bash
cp .env.example .env             # copy .env.example .env  on Windows
```

Then set `OPENAI_API_KEY` in `.env`. Everything else has a working default.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** |
| `LLM_MODEL` | `gpt-4o-mini` | Text answer generation |
| `VISION_MODEL` | `gpt-4o` | Figure questions and table fallback |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embeddings |
| `QDRANT_HOST` / `QDRANT_PORT` | `localhost` / `6333` | Vector store |
| `TEXT_CHUNK_SIZE` / `TEXT_CHUNK_OVERLAP` | `900` / `150` | Chunking |
| `RETRIEVAL_TOP_K` | `8` | Evidence blocks per answer — **tuned; see Limitations** |
| `DENSE_TOP_K` / `BM25_TOP_K` | `20` / `20` | Candidate windows |
| `RRF_K` / `ROUTER_BOOST` | `60` / `0.05` | Fusion |

`.env` is gitignored and must never be committed.

---

## Running locally

```bash
docker run -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant   # start Qdrant
python scripts/download_document.py                                       # fetch the filing
python -m app.cli ingest                                                  # extract, embed, index
python -m app.cli serve                                                   # http://localhost:8000
```

### CLI

```bash
python -m app.cli status                       # config and index health
python -m app.cli ingest --rebuild             # re-index from scratch
python -m app.cli query "What were total net sales in Q3 2022?"
python -m app.cli query "What images appear?" --type figure
python -m app.cli evaluate                     # full evaluation set
```

## Running with Docker

```bash
cp .env.example .env    # add your OPENAI_API_KEY
docker compose up --build
```

Starts Qdrant and the application, fetches the document, builds the index, and serves on
<http://localhost:8000>. If `OPENAI_API_KEY` is absent the server still starts and the UI
reports the missing key rather than the container exiting silently.

---

## Example queries

### Text

> **What does the filing say about the impact of COVID-19 on Apple's business?**
>
> The COVID-19 pandemic has had, and continues to have, a significant impact around the
> world, affecting Apple's business, results of operations, and financial condition…
>
> *Sources: Page 15 (PDF p. 18) — text — Quarterly Highlights*

### Tables

> **What were Apple's total net sales for the three months ended June 25, 2022?**
>
> Apple's total net sales for the three months ended June 25, 2022, were $82,959 million.
>
> *Sources: Page 15 (PDF p. 18) — table — Products and Services Performance*

### Figures

> **Does this filing contain any charts or graphs showing revenue trends?**
>
> The filing contains no charts or graphs showing revenue trends. It includes tables with
> revenue data but no visual charts or graphs.
>
> *Sources: PDF p. 1 — figure — Document summary*

### Unsupported — the refusal path

> **What were Apple's total net sales in fiscal year 2024?**
>
> **No supported answer found.** The filing does not provide information about Apple's
> total net sales for fiscal year 2024. It only includes net sales data for the three- and
> nine-month periods ended June 25, 2022, and June 26, 2021.

---

## Testing

```bash
pytest                      # 113 tests
pytest tests/unit           # no document or network required
pytest tests/integration    # runs against the real PDF, still no network
ruff check app tests scripts
```

Extraction is tested against the **real PDF** rather than a synthetic fixture, because the
document's defects *are* the specification. A hand-made fixture would have clean headers
and uninflated columns and would test none of the behaviour that matters. Tests skip
automatically if the PDF has not been downloaded.

---

## Project structure

```
app/
├── config/       pydantic-settings bound to .env
├── models/       Chunk, Provenance, discriminated payloads
├── ingestion/    loader, boilerplate detection, section tagging
├── extraction/   text, table headers, table normaliser, figures, page renderer
├── chunking/     per-content-type strategies
├── indexing/     embedder, Qdrant store, BM25, index builder
├── retrieval/    router, hybrid retriever, RRF fusion, context builder
├── generation/   prompts, answerer, citations
├── evaluation/   runner, metrics
├── api/          FastAPI + static frontend
├── cli/          ingest / query / evaluate / serve
└── errors.py     typed exception hierarchy

docs/             pdf-analysis · architecture · design-decisions · assessment-report
tests/            unit / integration / evaluation_questions.json
scripts/          download_document.py · entrypoint.sh
```

---

## Design decisions

**Header recovery is the core of the project.** Headers are recovered from the band above
each table by grouping fragments on x-overlap, merging them vertically (`June 25,` above
`2022`), and assigning group labels by midpoint boundaries — a group header is *centred
over* its columns, not spanning them. The search is floored at the preceding table's
bottom edge.

**Column collapse groups raw columns by recovered header.** This handles a staggered
layout where a value sits in a different raw column depending on whether a currency symbol
precedes it: raw column 1 holds `$` for iPhone but `7,382` for Mac. Symbols are read per
row, so a row the filing prints without `$` does not gain one.

**Tables are never split**, and a table whose structure fails validation is flagged rather
than trusted — it falls back to the page render.

**Query routing is a soft bias, never a filter.** A misrouted filter excludes the correct
evidence unrecoverably; a misrouted bias only reorders.

**Two models, split by cost and risk.** Text generation is frequent and mostly lookup;
vision is rare and hard. Paying for capability only where it is scarce.
