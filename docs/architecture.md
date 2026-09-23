# Architecture

**Phase 3 deliverable.** This design responds to measured properties of the source document.
Every non-obvious choice traces to a finding in [`pdf-analysis.md`](pdf-analysis.md), cited
inline as *(PA §n)*.

---

## 1. Design principles

1. **Extraction correctness before retrieval sophistication.** The dominant failure mode in
   this document is a table whose period headers were dropped (*PA §5, Defect 2*). No amount
   of reranking recovers a number whose column label was never indexed. Effort is allocated
   accordingly.
2. **Provenance is part of the data, not an afterthought.** Every unit of retrievable content
   carries its page, section, and content type from the moment it is extracted.
3. **Honest capability reporting.** A pathway that exists but is thinly evidenced is described
   as exactly that (*PA §4*).
4. **No complexity the document does not justify.** The corpus is ~70k characters (*PA §3*).
   Distributed vector stores, multi-stage agentic retrieval, and knowledge graphs are not
   warranted, and their absence is a design decision rather than an omission.
5. **Degrade, don't crash.** Every extraction stage has a defined fallback; the page-image
   render is the universal one.

---

## 2. Pipeline

```
                          ┌─────────────────────────────────────┐
     data/raw/*.pdf ─────▶│  INGESTION                          │
                          │  · load + validate                  │
                          │  · strip boilerplate      (PA §6)   │
                          │  · resolve printed page   (PA §2)   │
                          │  · tag sections           (PA §7)   │
                          └──────────────┬──────────────────────┘
                                         │  PageContext
                  ┌──────────────────────┼──────────────────────┐
                  ▼                      ▼                      ▼
         ┌────────────────┐    ┌──────────────────┐    ┌────────────────┐
         │ TEXT extractor │    │ TABLE extractor  │    │ FIGURE extractor│
         │ blocks + spans │    │ detect → NORMALISE│   │ xref + bbox     │
         │                │    │ (PA §5)          │    │ + caption       │
         └───────┬────────┘    └────────┬─────────┘    └───────┬────────┘
                 │                      │                      │
                 │              ┌───────▼────────┐             │
                 │              │ validation     │             │
                 │              │ fail ──────────┼────────────▶│ page render
                 │              └───────┬────────┘             │ fallback
                 │                      │                      │
                 └──────────────────────┼──────────────────────┘
                                        ▼
                          ┌─────────────────────────────────────┐
                          │  CHUNKING + METADATA                │
                          │  one Chunk model, three payloads    │
                          └──────────────┬──────────────────────┘
                                         ▼
                          ┌─────────────────────────────────────┐
                          │  INDEXING                           │
                          │  Qdrant (dense) ‖  BM25 (lexical)   │
                          └──────────────┬──────────────────────┘
                                         ▼
       query ──▶ ┌──────────────────────────────────────────────┐
                 │  RETRIEVAL                                   │
                 │  router (soft) → dense + BM25 → RRF fusion   │
                 │  → dedupe → context builder                  │
                 └──────────────┬───────────────────────────────┘
                                ▼
                 ┌──────────────────────────────────────────────┐
                 │  GENERATION                                  │
                 │  grounded prompt · refusal path · citations  │
                 └──────────────┬───────────────────────────────┘
                                ▼
                    Answer + Evidence(page, type) + Confidence
```

---

## 3. Data model

A single chunk type with a discriminated payload, rather than three parallel record types.
This keeps the index, the retriever, and the context builder generic while letting each
content type carry what it needs.

```python
class ContentType(str, Enum):
    TEXT   = "text"
    TABLE  = "table"
    FIGURE = "figure"


@dataclass(frozen=True)
class Provenance:
    document_id: str            # "AAPL_2022_Q3_10Q"
    pdf_page: int               # 1-based index into the PDF
    printed_page: int | None    # from the footer; None for cover/TOC/exhibits (PA §6)
    part: str | None            # "Part I — Financial Information"
    item: str | None            # "Item 2 — Management's Discussion and Analysis"
    section: str | None         # "Products and Services Performance"
    note: str | None            # "Note 9 — Segment Information and Geographic Data"
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class Chunk:
    chunk_id: str               # "AAPL_2022_Q3_10Q::p18::table::0"
    content_type: ContentType
    embed_text: str             # what is embedded and BM25-indexed
    display_text: str           # what is shown to the LLM and the user
    provenance: Provenance
    payload: TablePayload | FigurePayload | None
```

`embed_text` and `display_text` are deliberately separate. A table embeds best as its caption
plus a compact header/row summary; it presents best as clean Markdown. Forcing one string to
serve both degrades retrieval or readability.

### Table payload

```python
@dataclass(frozen=True)
class TablePayload:
    caption: str | None             # the "The following table shows…" sentence (PA §5)
    units: str | None               # "(In millions, except number of shares…)"
    column_headers: list[str]       # flattened two-level headers, e.g.
                                    # "Three Months Ended | June 25, 2022"
    rows: list[dict[str, str]]      # {row_label: ..., "Three Months Ended | June 25, 2022": "63,355", ...}
    markdown: str                   # normalised, renderable
    validation: TableValidation     # OK | WIDTH_MISMATCH | HEADERS_UNRESOLVED
```

Dual representation, as required by plan §7: `markdown` drives retrieval and human display;
`rows` gives the generation layer an unambiguous key→value lookup so "net sales for the three
months ended June 25, 2022" resolves to one cell rather than a guess among four columns.

### Figure payload

```python
@dataclass(frozen=True)
class FigurePayload:
    image_path: Path            # extracted raster
    page_render_path: Path      # full-page render, for multimodal queries
    caption: str | None         # nearest caption-like text, if any
    surrounding_text: str       # narrative context above/below
    width: int
    height: int
    is_decorative: bool         # heuristic: small, on cover, no caption
```

`is_decorative` matters for honesty. The Apple logo is flagged, so the system never implies it
has interpreted a data graphic (*PA §4*).

---

## 4. Ingestion layer

**Load and validate.** Open with PyMuPDF. An encryption dictionary without a user password is
valid input and must not be treated as failure (*PA §1*). Genuine password protection, a
non-PDF file, and a zero-page document each raise a distinct typed error.

**Strip boilerplate.** Remove the running head `Apple Inc.`, the footer
`Apple Inc. | Q3 2022 Form 10-Q | N`, and `See accompanying Notes to Condensed Consolidated
Financial Statements.` before chunking (*PA §6*). Left in, these three strings would appear in
a dozen chunks and receive a spurious similarity boost on any query naming Apple or the notes.

**Resolve page numbers.** Parse `N` from the footer to obtain the printed page. Where absent
(PDF 1–3, 26–28), `printed_page` is `None` and citations fall back to the PDF index, labelled
as such. The offset is *derived per page from the footer*, never hardcoded as −3 — the constant
holds for this filing but is a property of the document, not of 10-Qs in general.

**Tag sections.** With no PDF outline (*PA §1*), the hierarchy is recovered from `Arial-BoldMT`
spans ≥ 8.0 pt, which reproduced the full Table of Contents structure during inspection
(*PA §7*). Headings are matched against the known Part/Item vocabulary and carried forward as
running state, so every chunk inherits the section active at its position.

---

## 5. Table normalisation — the central component

This is where the document's real difficulty lives. The stage runs six ordered steps.

**Step 1 — Detect.** `page.find_tables()` yields candidate regions.

**Step 2 — Recover headers.** Expand a search band *above* the detected bbox top and extract
text lines there. Cluster spans by y to identify header rows, then assign each to columns by
x-overlap with the detected column boundaries. Two-level headers (a group header such as
`Three Months Ended` spanning two date columns) flatten to `Three Months Ended | June 25, 2022`.
This directly repairs Defect 2, without which every financial value is unlabelled.

**Step 3 — Collapse symbol and spacer columns.** Drop columns that are wholly empty; merge a
column containing only `$` into the value column on its right, and only `%` into the value
column on its left. Page 4's 12 columns reduce to 5, page 18's 18 to 6 (*PA §5, Defect 1*).

**Step 4 — Normalise values.** Parenthesised negatives `(10)` → `-10`, retaining the original
string alongside the parsed number. Thousands separators preserved in display, stripped for
parsing.

**Step 5 — Attach context.** Search upward for the units parenthetical `(In millions…)` and for
a caption sentence matching the near-templated `following table (shows|provides)…` pattern
(9 instances located in *PA §5*). Attach the enclosing bold heading. This restores contents,
periods, and units — precisely what extraction destroys.

**Step 6 — Validate.** Compare each row's populated-cell count against the table's modal width.
Mismatches mark the table `WIDTH_MISMATCH` (as page 16's `$ 119,455 $ 106,` will), which
triggers a pdfplumber re-parse and, if that also fails, routes the table to the page-render
fallback so a multimodal read can still answer from it. **Failures are recorded in the
ingestion report rather than silently swallowed.**

---

## 6. Chunking strategy

Uniform N-character splitting is explicitly rejected (plan §6). Strategy is per content type:

| Type | Unit | Rationale |
|---|---|---|
| Text | Section-aware: split at bold headings, then paragraphs, packed to ~900 chars with ~150 overlap | Headings are reliable semantic boundaries here (*PA §7*); paragraphs in this filing are short and self-contained |
| Table | **One chunk per table, never split** | Tables are small (largest ≈ 28 rows) and splitting one severs rows from headers — reintroducing Defect 2 by hand |
| Figure | One chunk per image: caption + surrounding narrative as the text proxy | The image itself is not embeddable by a text model; its context is |

Text chunks never cross a table boundary or a page boundary where a heading intervenes.

Why ~900 characters: mean page text is 2,501 characters (*PA §3*), so this yields roughly three
chunks per narrative page — fine enough for precise citation, coarse enough to preserve an
argument. The value is a configuration parameter and will be tuned against the evaluation set,
not asserted.

---

## 7. Indexing and retrieval

**Hybrid, because this corpus demands both.** Dense retrieval handles paraphrase
("how did the Mac business do?" → "Mac net sales decreased"). BM25 handles the exact tokens
that saturate financial queries — `iPhone`, `Note 5`, `16,070,752,000`, `June 25, 2022` — where
embeddings are weakest and where a wrong match is a wrong number.

- **Dense:** Qdrant collection over cosine distance, queried in **exact mode**. At ~200 chunks the
  HNSW approximation buys nothing, and exact search removes a source of non-determinism from the
  evaluation results.
- **Lexical:** `rank_bm25` over tokenised `embed_text`.
- **Fusion:** Reciprocal Rank Fusion (k=60). Chosen over weighted score blending because dense
  cosine scores and BM25 scores are not on a comparable scale, and RRF needs no per-corpus
  tuning.
- **Filtering:** the full `Provenance` record is stored as the Qdrant payload, so constraints on
  `content_type`, `section`, `note`, or page range are applied inside the engine rather than by
  post-filtering results in application code.

**Query routing is a soft bias, not a hard filter.** A query mentioning "figure" or "chart"
boosts figure chunks; one asking for a number boosts tables. It never *excludes* a content
type, because a misrouted query that filters out the correct evidence fails irrecoverably,
whereas a misrouted soft boost merely reorders. This is a deliberate guard against the routing
failure mode that plan §10 invites.

**Duplicate-content handling.** Segment figures appear in both Note 9 (p. 16) and MD&A (p. 19)
with overlapping values (*PA §7*). Deduplication is by content hash *and* provenance, so
near-identical chunks from different sections both survive to the ranking stage and the
citation names the one actually used.

---

## 8. Context construction and generation

The context builder deduplicates, groups evidence by content type, labels each block with its
provenance, and enforces a token budget. Tables enter as normalised Markdown plus their caption
and units. Figures enter as caption and surrounding text; when the query is visual, the rendered
page image is attached to a multimodal call.

**Two models serve these two paths** (see [D6](design-decisions.md#d6--generation-models)).
`gpt-4o-mini` handles text answer generation — the frequent path, and by design mostly lookup,
because the table normaliser has already resolved the labels and units the model would otherwise
have to infer. `gpt-4o` handles the multimodal calls: figure interpretation and the page-render
fallback for tables that fail validation. That path fires rarely and is the system's last resort
when structured extraction has failed, so it is the wrong place to economise. The consequence for
reporting is recorded in D6: answer quality is not uniform across content types, and the
evaluation must not present a single blended score as though one model produced it.

The system prompt enforces four behaviours:

1. Answer **only** from supplied evidence.
2. When evidence is absent or insufficient, **say so explicitly** — do not infer, do not fall
   back on world knowledge about Apple.
3. Cite the printed page and content type for every factual claim.
4. Report figures with their units as given.

Point 2 is the one most RAG systems get wrong, and the evaluation set includes negative cases
specifically to measure it (plan §13).

**Prompt injection.** Retrieved document content is untrusted input. It is wrapped in explicit
delimiters and the system prompt states that text inside them is data to be quoted, never
instructions to be followed. The generation call exposes no tools, so an injected instruction
has no action to trigger; output is rendered as plain text, never executed. For this
specific document the risk is close to nil — it is an SEC filing — but the mitigation is part
of the architecture because the ingestion path accepts arbitrary PDFs.

---

## 9. Citations

Every answer carries evidence entries of the form:

```
Sources:
  • Page 15 (PDF p. 18) — table — "Products and Services Performance"
  • Page 14 (PDF p. 17) — text  — "Quarterly Highlights"
```

Printed page leads because that is the number a reader sees on the page and the number the
Table of Contents uses; the PDF index follows for unambiguous lookup (*PA §2*). Getting this
backwards would make every citation in the system off by three.

---

## 10. Error handling

| Failure | Behaviour |
|---|---|
| Invalid / corrupt PDF | Typed `IngestionError`, actionable message, no traceback to user |
| Password-protected PDF | Distinct error; encryption *dictionary* alone is not an error (*PA §1*) |
| Missing API key | Detected at startup with a clear remediation message, not at first query |
| Empty / whitespace query | Rejected before any API call |
| No relevant retrieval | Explicit "no supporting evidence found" — never a fabricated answer |
| Table parse failure | Marked, re-parsed with pdfplumber, then page-render fallback |
| Missing figure file | Chunk retained with text context; image marked unavailable |
| LLM API failure | Bounded retry with backoff, then a clear degraded-mode message |
| Qdrant unreachable at startup | Retry with backoff — under Compose the app may start before Qdrant is ready; a transient connection refusal must not kill the process |
| Qdrant unreachable at query time | Clear service-unavailable message; the system does not silently fall back to lexical-only results, which would look like a working answer built on half the evidence |
| Collection missing or embedding-model mismatch | Instructs the user to run ingestion; does not silently query a stale or partial collection |

Full diagnostics go to logs; users see actionable messages.

---

## 11. Module structure

```
app/
├── config/        settings (pydantic-settings), .env binding
├── models/        Chunk, Provenance, payloads, enums
├── ingestion/     loader, boilerplate stripper, page resolver, section tagger
├── extraction/    text_extractor, table_extractor, table_normaliser,
│                  figure_extractor, page_renderer
├── chunking/      per-content-type strategies
├── indexing/      embedder, qdrant_store, bm25_store, index_builder
├── retrieval/     router, hybrid_retriever, fusion, context_builder
├── generation/    prompts, answerer, citation_formatter
├── evaluation/    runner, retrieval_metrics, answer_grader
├── api/           FastAPI app, routers, Pydantic request/response schemas
│   └── static/    frontend: index.html, styles.css, ES modules (no build step)
├── cli/           ingest / query / evaluate commands
└── errors.py      typed exception hierarchy
```

Each module is independently testable; extraction and retrieval have no knowledge of the LLM
provider, which is confined to `indexing/embedder.py` and `generation/`.

---

## 12. Interface contract

The frontend, the CLI, and the integration tests are three clients of the same service layer.
None of them reaches into pipeline internals.

| Endpoint | Purpose |
|---|---|
| `POST /api/query` | `{question, content_types?, top_k?}` → `{answer, refused, evidence[], telemetry}` |
| `GET /api/health` | Liveness plus Qdrant reachability, collection status, and the embedding model the collection was built with |
| `GET /api/examples` | Seeded example questions grouped as text / table / figure / unsupported, so the three required capabilities are demonstrable without the viewer having to invent queries |
| `GET /api/pages/{n}/render` | Rendered page image, for evidence display |
| `GET /api/figures/{figure_id}` | Extracted figure image |
| `GET /` | Static frontend |

Each `evidence[]` entry carries `content_type`, `printed_page`, `pdf_page`, `section`, `rank`,
and — for tables — the normalised headers and rows, so the client renders a real table rather
than a blob of Markdown. `telemetry` carries retrieval and generation latency, chunk counts, and
the content-type mix, which is what plan §29 asks to be recorded.

`refused` is an explicit boolean rather than something the client infers from the answer text.
A refusal is a distinct outcome and must be representable as one.

---

## 13. Deliberately not built

Stated so their absence reads as a decision rather than an oversight:

- **Knowledge graph / entity linking** — no multi-document reasoning requirement.
- **Approximate nearest neighbour search** — Qdrant's HNSW index is bypassed in favour of exact
  search; the approximation buys nothing at this scale and costs determinism.
- **Agentic multi-hop retrieval loops** — the evaluation set will show whether single-shot
  hybrid retrieval suffices; adding loops before measuring would be unjustified.
- **Fine-tuning** — no training data, and no evidence a general model is insufficient here.
- **OCR** — measured as unnecessary; every page has a text layer (*PA §3*).
- **Cross-encoder reranking** — *deferred, not rejected.* It will be implemented and measured
  against the baseline, and retained only if it demonstrably helps.
