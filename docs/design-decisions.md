# Design Decisions

**Phase 4 deliverable.** Each decision records the alternatives considered, the reason for the
choice, and what would change the answer. Choices driven by measured properties of the source
document cite [`pdf-analysis.md`](pdf-analysis.md) as *(PA §n)*.

Decisions marked **DEFERRED** are not yet settled and will be resolved by measurement during
implementation. They are listed here so the open questions stay visible rather than being
quietly resolved later.

---

## D1 — PDF processing library

**Chosen: PyMuPDF as primary, pdfplumber as a second opinion on failed tables.**

| Option | Assessment |
|---|---|
| **PyMuPDF** | Fast; exposes text, spans with font/size, images with xrefs, vector drawings, and `find_tables()` through one API. Font metadata is what makes the heading heuristic work (*PA §3*), and `get_image_rects` gives figure placement. Already validated: found all 31 tables during inspection. |
| **pdfplumber** | Superior fine-grained table control (explicit line/edge strategies) but markedly slower and no image/font API of comparable depth. Ideal as a targeted fallback, poor as the primary. |
| **Unstructured** | Strong general-purpose partitioner, but heavy dependencies and its table output for this file would still require the EDGAR column-collapse work (*PA §5, Defect 1*). Adds weight without removing the actual problem. |
| **Docling** | Excellent layout models and genuinely good table structure recovery. Rejected on cost/benefit: large model downloads and slow first run, to solve a problem that is tractable here because the document is born-digital with clean font signals. Worth revisiting for scanned or complex-layout corpora. |
| **Camelot / Tabula** | Camelot's lattice mode suits ruled tables, and this document is heavily ruled. Rejected because it handles only tables — a second library would still be needed for text and images — and its Ghostscript/Java dependencies complicate the container. |
| **LlamaParse / cloud parsers** | Good quality, but introduces a network dependency and per-page cost for a task solvable locally, and sends the document to a third party. |

**Why not a single library for everything:** no available parser correctly recovers headers that
sit outside the detected table bbox (*PA §5, Defect 2*) — that repair is document-specific logic
regardless of parser. Since the hard part is ours either way, the sensible criterion is which
library gives the best primitives to build it on, and that is PyMuPDF.

**Would change if:** the corpus included scanned pages (→ Docling or an OCR stage) or
multi-column layouts (→ a layout model).

---

## D2 — Embedding model

**Chosen: OpenAI `text-embedding-3-small` (1536-d), configurable.**

| Option | Assessment |
|---|---|
| **text-embedding-3-small** | Strong quality-per-cost; 1536-d keeps the vector collection light. Embedding the whole corpus (~200 chunks) costs a fraction of a cent, so ingestion can be re-run freely while chunking parameters are tuned — which matters because those parameters are being set empirically rather than guessed. |
| **text-embedding-3-large** | Higher quality, 3072-d, ~6.5× the price. Meaningful on large or subtle corpora; on 200 chunks of a single filing, the retrieval bottleneck is table representation quality, not embedding ceiling. |
| **Local (bge-small-en-v1.5 / all-MiniLM-L6-v2)** | Free, offline, competitive on MTEB. Rejected as primary because the provider is already OpenAI, so a local model adds a torch dependency and a model download to the container for no quality gain. |

Uses the same API key as generation, so no additional credential. The model name is a
configuration value; the collection records which model produced it, and a mismatch on load is
an error rather than a silent correctness bug.

**DEFERRED:** whether `-large` measurably improves retrieval here. Testable by re-indexing and
re-running retrieval metrics. Will be reported as measured, or explicitly as not measured.

---

## D3 — Vector store

**Chosen: Qdrant, run locally as a Docker container.**

| Option | Assessment |
|---|---|
| **Qdrant** | Purpose-built vector database with native payload filtering and indexing, a clean Python client, and a single-line Compose service with a persistent volume. Payload filters map directly onto the provenance model — filtering by `content_type`, `section`, or page range happens inside the engine rather than in application code. |
| **FAISS flat** | Exact search, no server, persists as a file. Genuinely sufficient at ~200 vectors and the lightest option. Not chosen: it is a library, not a database, so metadata filtering, persistence, and inspection all become application code. |
| **Chroma** | Pleasant API with built-in persistence and filtering. Rejected as the middle option that is neither the lightest (FAISS) nor the most production-shaped (Qdrant). |
| **pgvector** | Sensible when a relational database is already in the stack. It is not. |

**Honest framing of this choice.** At ~200 vectors, FAISS would be entirely adequate and every
query would be effectively instant either way, so Qdrant is not justified by scale — and this
document does not pretend otherwise. It is justified by three things that do apply: native
payload filtering the architecture genuinely uses, a realistic separation between the
application and its datastore, and a Docker Compose deployment that is substantive rather than
decorative. The cost is one additional service to run, which the container setup absorbs.

Search uses **exact mode** rather than the HNSW approximation. At this collection size the
approximation buys nothing, and exact search removes a source of non-determinism from the
evaluation results.

**Would change if:** the deployment had to be a single process with no services, in which case
FAISS behind the same `VectorStore` interface is a drop-in substitute.

---

## D4 — Lexical retrieval

**Chosen: `rank_bm25` (BM25Okapi), in-process.**

Hybrid retrieval is not decoration here. Financial queries are dense with exact tokens —
`Note 5`, `June 25, 2022`, `16,070,752,000`, `iPhone` — where embeddings are weakest and where a
near-miss returns the wrong number rather than a vaguely worse answer. BM25 is precise on exactly
these.

Qdrant's own sparse-vector support was considered, since it would consolidate both retrievers in
one engine. Rejected for the baseline: it requires generating sparse representations at ingestion
and adds moving parts, while `rank_bm25` over ~200 chunks is a few milliseconds in-process and
trivially unit-testable. Elasticsearch and Tantivy were rejected as infrastructure out of
proportion to the corpus.

---

## D5 — Fusion strategy

**Chosen: Reciprocal Rank Fusion, k = 60.**

Weighted score blending was rejected because dense cosine similarities and BM25 scores occupy
different, corpus-dependent scales; blending them requires a normalisation constant tuned per
corpus, which is one more thing to get wrong and to justify. RRF uses only rank position, needs
no tuning, and is robust when one retriever performs poorly on a given query.

**DEFERRED:** whether RRF beats a tuned weighted blend on this evaluation set. RRF is the
baseline; a blend will only be adopted on evidence.

---

## D6 — Generation models

**Chosen: a two-model split — `gpt-4o-mini` for text answer generation, `gpt-4o` for vision.**

| Path | Model | When it runs | Why this model |
|---|---|---|---|
| Text answer generation | `gpt-4o-mini` | Every query | The high-volume path. Cheap and fast enough that full evaluation runs can be repeated freely while tuning. Adequate for grounded lookup and comparison once the architecture has delivered labelled columns and resolved units. |
| Visual interpretation | `gpt-4o` | Figure queries, and the page-render fallback when a table fails validation (*PA §5, Defect 3*) | Visual reading of a dense financial page is the hardest inference in the system and the least tolerant of error. This path fires rarely, so the stronger model's cost is bounded. |

**Why the split is the right shape.** These two paths have opposite cost/quality profiles. Text
generation is frequent and, by design, mostly lookup — the table normaliser does the hard work
upstream, so the model reads a labelled value rather than inferring one. Vision is infrequent and
genuinely hard — it is a last-resort route used precisely when structured extraction has already
failed, so it is the wrong place to economise. Paying for capability only where it is scarce is
better engineering than applying one model uniformly.

**Honest limitations, to be measured rather than assumed:**

- `gpt-4o-mini` is weaker on multi-step arithmetic. The design compensates by pushing correctness
  upstream into extraction, but where evaluation exposes reasoning failures attributable to model
  capability rather than retrieval, they will be **reported as such** — not hidden, not excused.
- Refusal discipline on unsupported questions is a known weak point of smaller models. The
  evaluation set's negative cases exist specifically to measure it.
- Using two models means answer quality is not uniform across content types. Any comparison of
  text-path versus figure-path accuracy must account for this, and the evaluation write-up will
  state it rather than presenting a single blended score as if one system produced it.

Both identifiers are `.env` values (`LLM_MODEL`, `VISION_MODEL`), so either can be substituted
for a final run without a code change.

---

## D7 — Reranking

**DEFERRED pending measurement. Baseline ships without it.**

Plan §19 asks whether a reranker adds measurable value. Adopting one before measuring would
contradict that instruction. The intended sequence: establish hybrid-retrieval metrics, add a
cross-encoder, re-measure, keep it only if it helps.

Honest constraint: a local cross-encoder introduces a torch dependency and a model download,
which is real weight to add to the container. That cost is only worth paying against demonstrated
gain — and at ~200 chunks with top-k around 8, there may not be enough candidates for reranking
to change much. **This will be reported as measured, or explicitly as not measured.**

---

## D8 — Orchestration framework

**Chosen: direct implementation over the provider SDK, with thin internal interfaces.**

| Option | Assessment |
|---|---|
| **Direct** | Every stage — chunking, fusion, context assembly, prompting — is visible and unit-testable. The custom work this document actually needs (header recovery, column collapse, units attachment) sits outside what any framework provides, so a framework would wrap the hard part rather than solve it. |
| **LangChain** | Fast scaffolding and a large ecosystem. Rejected here: its abstractions would obscure precisely the RAG mechanics this assessment is meant to demonstrate, and debugging retrieval behaviour through layers of indirection costs more than it saves at this size. |
| **LlamaIndex** | Genuinely strong on document RAG, with good built-in table and node-metadata handling — the closest call of the three. Rejected on the same reasoning: the document-specific extraction repair still has to be hand-written, and the remaining orchestration is a few hundred lines. |

A considered trade-off, not a dismissal. At larger scope — many document types, many retrieval
strategies, a team maintaining it — the calculus favours LlamaIndex. For a single document, a
bounded scope, and an assessment that explicitly grades demonstrated understanding and
maintainability, direct implementation is the better fit.

**Interfaces stay thin regardless** (`Embedder`, `VectorStore`, `Retriever`, `Answerer`), so a
framework, a different vector database, or a different provider could be substituted without
rewriting the pipeline.

---

## D9 — Interface

**Chosen: FastAPI backend serving a purpose-built HTML/CSS/JavaScript frontend, plus a CLI.
Both call one shared service layer.**

| Option | Assessment |
|---|---|
| **FastAPI + custom frontend** | A real HTTP API with typed Pydantic request/response schemas and automatic OpenAPI docs, plus full control over the interface. The API is independently testable and independently useful — the frontend is one client, the CLI is another, and integration tests are a third. |
| **Streamlit** | Fastest to build, but the result looks like a prototype: constrained layout, visible reruns, and little control over presentation. Rejected — interface quality is part of what this system is judged on. |
| **Gradio** | Same objection as Streamlit, with less layout control. |
| **React / Vue with a bundler** | Powerful, but requires a Node toolchain inside the Docker image and a build step to maintain, for what is a single-page query interface. The complexity is not repaid. |

**No build step.** The frontend is hand-written HTML, CSS, and ES modules served as static files
by FastAPI. Modern CSS — grid, custom properties, container queries — produces a polished result
without a bundler, and it keeps the repository clone-and-run: no `npm install`, no `node_modules`,
no Node layer in the image.

**The interface is not decoration — it makes the RAG system legible.** A plain answer box would
hide exactly what this assessment is meant to demonstrate. The design surfaces the retrieval
pipeline directly:

- Each answer is paired with an **evidence panel**: one card per retrieved chunk, badged by
  content type, showing printed page, section, and fusion rank.
- **Table evidence renders as a table**, from the normalised representation — which makes the
  header-recovery work of *PA §5* visible rather than merely claimed.
- **Figure evidence shows the extracted image and its page render**, so a viewer can check the
  visual interpretation against the source instead of taking it on trust.
- **Refusals are a distinct visual state**, not an answer-shaped paragraph. When the system finds
  no supporting evidence it must look different from when it found some.
- **Retrieval telemetry is shown** — chunk count, latency, content-type mix — feeding plan §29
  from the interface rather than from a separate benchmark script.

**Honest cost.** This is meaningfully more work than Streamlit, and that time is not free given
the schedule. The judgement is that a professional interface plus a genuine HTTP API is worth it
here, both because the assignment is evaluated on the whole system and because the API boundary
improves testability regardless of how the UI looks.

The CLI remains the reproducible path for ingestion, evaluation, and scripted testing; it does not
depend on the web layer.

---

## D10 — Configuration, testing, packaging

- **Configuration:** `pydantic-settings` bound to `.env`, with a committed `.env.example`. All
  tunables — `LLM_MODEL`, `VISION_MODEL`, embedding model, chunk size, overlap, top-k, fusion
  constant, Qdrant host/port, collection name, paths — are centralised; no magic numbers
  scattered through modules, no secrets in source (plan §20).
- **Testing:** `pytest`, split into `unit/` and `integration/`. Extraction and chunking are
  tested against the real PDF because its defects (*PA §5*) are the specification; LLM calls are
  mocked in unit tests and exercised for real in a small number of end-to-end tests.
- **Containerisation:** Docker Compose runs two services — Qdrant with a persistent volume, and
  the application. The index is built at run time rather than baked into the image, keeping the
  image small and the build reproducible. The application must tolerate Qdrant not yet being
  ready and retry rather than crash on startup.
- **Version control:** `.gitignore` excludes `.env`, the venv, generated indexes, Qdrant storage,
  and caches (plan §26). The repository stays clone-and-run.

---

## Decision summary

| # | Decision | Choice | Status |
|---|---|---|---|
| D1 | PDF processing | PyMuPDF + pdfplumber fallback | Settled |
| D2 | Embeddings | `text-embedding-3-small`, configurable | Settled; `-large` comparison deferred |
| D3 | Vector store | **Qdrant** (Docker, exact search) | Settled |
| D4 | Lexical retrieval | `rank_bm25` | Settled |
| D5 | Fusion | RRF (k=60) | Settled; weighted blend deferred |
| D6 | Generation | **`gpt-4o-mini`** text · **`gpt-4o`** vision | Settled |
| D7 | Reranking | None in baseline | **Deferred — measure first** |
| D8 | Framework | Direct implementation | Settled |
| D9 | Interface | **FastAPI + custom HTML/CSS/JS frontend** (no build step) + CLI | Settled |
| D10 | Config / tests / Docker | pydantic-settings, pytest, Compose (app + Qdrant) | Settled |
