# Multimodal RAG over an SEC Form 10-Q

A retrieval-augmented generation system that answers questions about the **text**,
**tables**, and **figures** of Apple Inc.'s Form 10-Q for the quarter ended
June 25, 2022.

> **Status: in development.** This README grows as the system is built. Full
> installation, usage, evaluation, and Docker instructions land in Phase 18.

## Why this document is harder than it looks

It is a born-digital PDF converted from EDGAR HTML, and that conversion leaves
three defects that a naive RAG pipeline silently inherits:

1. **Tables lose their column headers.** The detected table region on page 4
   starts *below* the `Three Months Ended` / `Nine Months Ended` header band, so
   `63,355` is extracted with no indication of which period it belongs to.
2. **Columns are inflated two to three times over.** `$` and `%` each occupy
   their own column, padded with empty spacers — 12 extracted columns for 5
   logical ones.
3. **Units live outside the table.** `(In millions…)` sits in the narrative
   above it. Lose that line and Apple's quarterly net sales read as \$82,959
   instead of \$82.96 billion.

There is also **no figure to retrieve**: the document contains exactly one raster
image across 28 pages, and it is the Apple logo. The figure pathway is built
properly and reported honestly rather than quietly redefined to mean "tables".

Full measurements: [`docs/pdf-analysis.md`](docs/pdf-analysis.md).

## Documentation

| Document | Contents |
|---|---|
| [`docs/pdf-analysis.md`](docs/pdf-analysis.md) | Measured properties of the source PDF and the extraction challenges they imply |
| [`docs/architecture.md`](docs/architecture.md) | Pipeline, data model, retrieval and generation design, interface contract |
| [`docs/design-decisions.md`](docs/design-decisions.md) | D1–D10: technology choices, rejected alternatives, open questions |

## Stack

| Layer | Choice |
|---|---|
| PDF processing | PyMuPDF, with pdfplumber as fallback on failed tables |
| Embeddings | OpenAI `text-embedding-3-small` |
| Vector store | Qdrant (Docker), exact search, provenance as payload |
| Lexical retrieval | BM25 (`rank_bm25`) |
| Fusion | Reciprocal Rank Fusion |
| Text generation | `gpt-4o-mini` |
| Vision | `gpt-4o` |
| API | FastAPI with typed Pydantic schemas |
| Frontend | HTML, CSS, ES modules — no build step |

Every choice, including the ones rejected, is justified in
[`docs/design-decisions.md`](docs/design-decisions.md).

## Quick start

```bash
python -m venv venv
venv\Scripts\activate           # Windows;  source venv/bin/activate on Unix
pip install -r requirements.txt

copy .env.example .env          # cp on Unix — then add your OPENAI_API_KEY
python scripts/download_document.py
```

Remaining steps (ingestion, serving, evaluation) are added as those phases land.
