# PDF Analysis — Apple Inc. Form 10-Q, Q3 FY2022

**Phase 2 deliverable.** Everything below was measured against the actual file with PyMuPDF
before any architecture was proposed. No characteristic is assumed; where a property was not
measured, it is marked as such.

- **File:** `data/raw/2022_Q3_AAPL.pdf` (266,240 bytes)
- **Source:** `docugami/KG-RAG-datasets` → `sec-10-q/data/v1/docs/2022 Q3 AAPL.pdf`
- **Inspected:** 23 September 2026, PyMuPDF 1.28.2 / pdfplumber 0.11.10, Python 3.11.5

---

## 1. Document identity and provenance

| Property | Value |
|---|---|
| Pages | 28 |
| PDF version | 1.4 |
| Title (metadata) | `0000320193-22-000070` (SEC accession number) |
| Subject | Form 10-Q filed 2022-07-29 for the period ending 2022-06-25 |
| Author | EDGAR Online, a division of Donnelley Financial Solutions |
| Creator | EDGAR Filing HTML Converter |
| Producer | EDGRpdf Service w/ EO.Pdf 22.0.40.0 |
| Encryption | Standard V2 R3 128-bit RC4 |
| `is_encrypted` / `needs_pass` | `False` / `0` |
| Embedded outline (TOC bookmarks) | 0 entries |

Two facts here shape the whole ingestion design.

**The file is a converted HTML filing, not an authored PDF.** The producer chain is
EDGAR's HTML-to-PDF converter. Every visual table in this document was an HTML `<table>`,
and the converter emitted each cell — including currency symbols and percent signs — as an
independently positioned text run. This is the direct cause of the column-inflation defect
documented in §5.

**The file carries an encryption dictionary but no user password.** `is_encrypted` is
`False` and `needs_pass` is `0`, so it opens normally; the RC4 entry is a permissions flag
only. The ingestion layer must not treat the presence of an encryption dictionary as a
failure, but it must still handle genuinely password-protected input gracefully.

**There are no PDF bookmarks.** Section structure has to be recovered from the rendered
page content (§7), not read from an outline.

---

## 2. Page map

Printed page numbers differ from PDF page indices. The footer
`Apple Inc. | Q3 2022 Form 10-Q | N` appears on PDF pages 4–25 and establishes a constant
offset of **3** (PDF page 4 = printed page 1). The Table of Contents on PDF page 3 cites
*printed* numbers — it lists MD&A as page 14, which is PDF page 17. Verified at three
independent points: PDF 9 → printed 6, PDF 16 → printed 13, PDF 25 → printed 22.

| PDF | Printed | Section | Chars | Tables | Images |
|---:|---:|---|---:|---:|---:|
| 1 | — | Cover page (Form 10-Q, registrant details) | 2,667 | 0 | 1 |
| 2 | — | Cover page cont. (shell/growth-company checkboxes, share count) | 469 | 0 | 0 |
| 3 | — | Table of Contents | 586 | 2 | 0 |
| 4 | 1 | Condensed Consolidated Statements of Operations | 1,518 | 1 | 0 |
| 5 | 2 | Condensed Consolidated Statements of Comprehensive Income | 1,245 | 1 | 0 |
| 6 | 3 | Condensed Consolidated Balance Sheets | 1,695 | 1 | 0 |
| 7 | 4 | Condensed Consolidated Statements of Shareholders' Equity | 1,508 | 1 | 0 |
| 8 | 5 | Condensed Consolidated Statements of Cash Flows | 2,109 | 1 | 0 |
| 9 | 6 | Notes — Note 1 (Accounting Policies), Earnings Per Share | 2,654 | 1 | 0 |
| 10 | 7 | Note 2 — Revenue | 2,468 | 1 | 0 |
| 11 | 8 | Note 3 — Financial Instruments; Cash & Marketable Securities | 3,294 | 2 | 0 |
| 12 | 9 | Derivative Instruments and Hedging | 3,276 | 2 | 0 |
| 13 | 10 | Accounts Receivable; Note 4 — Financial Statement Details | 2,953 | 3 | 0 |
| 14 | 11 | Other Income/(Expense); Note 5 — Debt; Note 6 — Shareholders' Equity | 2,617 | 2 | 0 |
| 15 | 12 | Note 7 — Benefit Plans; Note 8 — Commitments and Contingencies | 2,624 | 3 | 0 |
| 16 | 13 | Note 9 — Segment Information and Geographic Data | 1,482 | 2 | 0 |
| 17 | 14 | Item 2 — MD&A; Quarterly Highlights | 4,535 | 0 | 0 |
| 18 | 15 | Products and Services Performance | 3,582 | 1 | 0 |
| 19 | 16 | Segment Operating Performance | 3,957 | 1 | 0 |
| 20 | 17 | Gross Margin | 2,838 | 2 | 0 |
| 21 | 18 | Operating Expenses; Other Income; Provision for Income Taxes | 3,075 | 3 | 0 |
| 22 | 19 | Liquidity and Capital Resources; Items 3 and 4 | 4,896 | 0 | 0 |
| 23 | 20 | Part II — Legal Proceedings; Risk Factors; Item 2 | 3,915 | 1 | 0 |
| 24 | 21 | Items 3–6; Exhibit index | 1,507 | 0 | 0 |
| 25 | 22 | Signature | 342 | 0 | 0 |
| 26 | — | Exhibit 31.1 — CEO certification | 3,263 | 0 | 0 |
| 27 | — | Exhibit 31.2 — CFO certification | 3,277 | 0 | 0 |
| 28 | — | Exhibit 32.1 — Section 1350 certifications | 1,679 | 0 | 0 |

Section names are taken from bold spans ≥ 8.0 pt, cross-checked against the Table of Contents.

---

## 3. Text layer

| Measurement | Value |
|---|---|
| Total extractable text | 70,031 characters |
| Mean per page | 2,501 characters |
| Pages with < 50 characters | none |
| Scanned / image-only pages | none |

**The document is fully born-digital with a complete text layer. OCR is not required
anywhere.** This was verified rather than assumed: every one of the 28 pages yields
extractable text, and the single raster image (§6) is decorative.

The corpus is small — roughly 18–20k tokens of text. That matters for design: the entire
document would fit inside a modern long-context window. Retrieval still earns its place
here (precision, citation, cost, and demonstrating RAG competence for the assessment), but
the architecture must not pretend to solve a scale problem this corpus does not have. This
is stated explicitly rather than papered over.

### Fonts

| Font | Size | Spans | Role |
|---|---:|---:|---|
| ArialMT | 8.1 | 1,546 | Body narrative text |
| ArialMT | 7.2 | 388 | Table cell values |
| Arial-BoldMT | 7.2 | 268 | Table row/column labels |
| Arial-BoldMT | 8.1 | 125 | Section and note headings |
| Arial-ItalicMT | 8.1 | 37 | Emphasis, defined terms |
| SegoeUISymbol | 9.0 | 12 | Checkbox glyphs (☐/☒) on the cover page |
| Arial-BoldMT | 15.3 / 18.0 | 1 each | Cover page title |

A single font family at four sizes. Heading detection is therefore tractable:
`Arial-BoldMT` at ≥ 8.0 pt reliably identifies section and note headings, and this rule
recovered the full structure in §7 without a layout model. The 7.2 pt / 8.1 pt split also
cleanly separates table content from narrative text.

### Layout

Single-column narrative throughout. No multi-column body text, no sidebars, no footnote
apparatus at the page foot — explanatory notes appear as numbered references `(1)`, `(2)`,
`(3)` immediately beneath their table.

---

## 4. Figures — the decisive finding

**The document contains exactly one raster image in 28 pages, and it is the Apple logo.**

| Property | Value |
|---|---|
| Location | PDF page 1, xref 5 |
| Format | JPEG, 46 × 56 px, 8 bpc |
| Placement rect | `(294.3, 261.2, 318.6, 291.6)` — centred, cover page |
| Content | The Apple corporate logo (visually confirmed after extraction) |

Searching all 28 pages for `figure|fig.|chart|graph|exhibit|diagram|image|photo` returns
**7 matches, every one of which is the word "Exhibit"** in the exhibit index on page 24 or
the exhibit certification titles on pages 26–28. There is not a single occurrence of
"Figure", "Chart", or "Graph" anywhere in the document.

The 5,215 vector drawing operations are **not charts**. They are table rules: cell borders,
underlines beneath column headers, and the double-underline convention marking financial
statement totals. Their distribution confirms this — the count per page tracks table
density almost exactly, peaking at 1,013 on page 11 (two dense marketable-securities
tables) and falling to 4 on pages 17 and 22, which are pure narrative.

### What this means for the assessment

The assignment requires answering questions about "figures present in the PDF." This
document has no data figures to interrogate. That is a property of SEC quarterly filings in
general — a 10-Q is a text-and-tables instrument; charts belong to the glossy annual report,
not the statutory filing.

There are three honest responses, and only one of them is defensible:

1. **Quietly redefine "figures" as tables** and claim the requirement is met. Rejected —
   it misrepresents the system, and the plan's §32 forbids exactly this.
2. **Build nothing for figures** and state the document has none. Rejected — the assignment
   asks for a capability, and the absence of test data is not the absence of a requirement.
3. **Build a genuine, exercised figure pathway and report the corpus honestly.** Adopted.

The adopted approach: implement real image extraction with full provenance (page, bounding
box, placement, nearest caption text, enclosing section), route visual questions through a
multimodal model against the rendered page region, demonstrate the pathway end-to-end on
the one image that exists, and state plainly in the report that this corpus is
figure-sparse so the pathway is architecturally complete but thinly evidenced.

A page-image fallback strengthens this materially. Because every financial statement is
laid out visually, rendering a page to an image and asking a multimodal model about it is a
genuine visual-retrieval route — and it doubles as the safety net for any table whose
structure the parser mangles (§5). The figure pathway is therefore not dead weight built to
satisfy a checkbox; it is load-bearing for table correctness too.

---

## 5. Tables — the core engineering problem

PyMuPDF's detector finds **31 tables across pages 3–23**. Extraction quality is where the
real work is, and three distinct defects were confirmed by inspection.

### Defect 1 — Column inflation from the HTML conversion

The Statements of Operations on PDF page 4 is logically a 5-column table: a row label plus
four periods. It extracts as **28 rows × 12 columns**, because the converter emits `$` as
its own column and pads with empty separator columns:

```
| Products | $ | 63,355 |  | $ | 63,948 |  | $ | 245,241 |  | $ | 232,309 |
```

Page 18 is worse: 6 logical columns become **18**, because each percentage change also
splits its number from its `%` sign:

```
| iPhone (1) | $ | 40,665 |  | $ | 39,570 |  | 3 | % |  | $ | 162,863 | ...
```

Roughly 60% of extracted cells are structurally empty or hold a bare symbol. Fed to an
embedding model as-is, this is close to noise.

### Defect 2 — Header rows fall outside the detected table

This is the serious one. On page 4 the detected bbox starts at `y = 207.2`, but the
two-level period header — `Three Months Ended` / `Nine Months Ended` spanning
`June 25, 2022` and `June 26, 2021` — sits **above** that boundary. The extracted table
begins at the row `Products`.

The consequence: the value `63,355` is retrieved with **no indication of which period it
belongs to**. Four columns of figures arrive completely unlabelled. A system built on this
extraction will answer "what were Apple's Q3 2022 product sales?" by guessing a column, and
will be wrong roughly three times in four. Any table strategy that does not solve header
recovery is broken regardless of how good the retrieval is.

### Defect 3 — Cell merge failures

Page 16, table 1 collapses three cells into one and truncates a value:

```
| Segment operating income |  |  |  | $ | 31,451 |  | $ 119,455 $ 106, |  |
```

`$ 119,455 $ 106,` should be two separate values. Detected on 1 of 3 sampled tables, so
this is not rare. The system needs a validation pass that flags rows whose parsed cell count
disagrees with the table's modal width, and a fallback path for the rows that fail.

### The units problem

Financial values here are meaningless without their scale, and the scale is never inside the
table. It appears in the narrative immediately above it:

> "(In millions, except number of shares which are reflected in thousands and per share amounts)"

Page 18 uses a different qualifier: "(dollars in millions)". A retrieved table fragment
stripped of this line invites a two-orders-of-magnitude error — reporting Apple's quarterly
net sales as \$82,959 rather than \$82.959 billion. The units string must travel with the
table as metadata, not be left to chance in an adjacent chunk.

### Captions

This document uses **no numbered captions**. There is no "Table 1:" anywhere. Tables are
introduced by a narrative sentence immediately preceding them, in a highly regular form —
9 instances were located:

| Page | Caption sentence (truncated) |
|---:|---|
| 9 | "The following table shows the computation of basic and diluted earnings per share…" |
| 12 | "The following table shows the fair value of the Company's non-current marketable debt securities, by contractual maturity, as of June 25, 2022 (in millions)…" |
| 14 | "The following table shows the detail of other income/(expense), net…" |
| 14 | "The following table provides a summary of cash flows associated with the issuance and maturities of Commercial Paper…" |
| 15 | "The following table shows share-based compensation expense and the related income tax benefit…" |
| 15 | "The following table shows changes in the Company's accrued warranties and related costs…" |
| 16 | "The following table shows information by reportable segment…" |
| 18 | "The following table shows net sales by category…" |
| 19 | "The following table shows net sales by reportable segment…" |

This is a significant asset. These sentences carry **exactly what the extraction loses**:
what the table contains, which periods it covers, and the units. Pairing each table with its
preceding caption sentence and its enclosing bold heading reconstructs most of the semantic
context that column inflation destroys — and it is far more reliable here than generic
caption heuristics, because the phrasing is near-templated.

Financial statements on pages 4–8 have no such sentence; their context comes from the
ALL-CAPS bold statement title and the units line beneath it.

---

## 6. Headers, footers, and boilerplate

| Artifact | Occurrences | Handling |
|---|---|---|
| `Apple Inc.` as running head | 6 pages | Strip before chunking |
| `Apple Inc. \| Q3 2022 Form 10-Q \| N` footer | PDF 4–25 | Strip; parse `N` as the printed page number |
| `See accompanying Notes to Condensed Consolidated Financial Statements.` | PDF 4–8 | Strip |

Pages 1–3 and 26–28 carry no footer, which is why the printed-page mapping is defined only
for PDF pages 4–25. Citations for the cover, TOC, and exhibit pages must fall back to the
PDF index and be labelled as such.

Leaving this boilerplate in place would put the same three sentences into a dozen chunks and
give every one of them a spurious similarity boost on any query mentioning Apple or the
notes.

---

## 7. Section structure

With no PDF outline, structure is recovered from bold spans ≥ 8.0 pt. This yields a clean
two-level hierarchy that matches the Table of Contents:

- **Part I — Financial Information**
  - Item 1 — Financial Statements → four statements (pp. 4–8), then Notes 1–9 (pp. 9–16)
  - Item 2 — MD&A (pp. 17–22), with sub-headings: Quarterly Highlights, Products and
    Services Performance, Segment Operating Performance, Gross Margin, Operating Expenses,
    Provision for Income Taxes, Liquidity and Capital Resources
  - Items 3 and 4 (p. 22)
- **Part II — Other Information** (pp. 23–24), Items 1, 1A, 2, 3, 4, 5, 6
- **Signature** (p. 25), **Exhibits 31.1 / 31.2 / 32.1** (pp. 26–28)

Every chunk can therefore be tagged with Part, Item, and Note or sub-heading. This supports
metadata-filtered retrieval — "what does Note 5 say about term debt" can be scoped to the
right pages instead of relying on embedding similarity alone.

A note on ambiguity: several headings **repeat**. "Other Income/(Expense), Net" appears on
both page 14 (Note 4 detail) and page 21 (MD&A discussion). Segment figures appear in Note 9
(p. 16) and again in MD&A (p. 19), with overlapping values. Retrieval will surface
near-identical content from two locations, and the answer must cite the right one. This is a
genuine hard case for the evaluation set, not a hypothetical.

---

## 8. Extraction challenges → architecture implications

| # | Challenge | Evidence | Implication |
|---|---|---|---|
| 1 | Table columns inflated ~2–3× by symbol/spacer columns | p4: 12 cols for 5 logical; p18: 18 for 6 | Normalise columns before representation; drop symbol-only and empty columns |
| 2 | Period headers excluded from detected table bbox | p4 bbox starts below the header band | Recover headers by expanding the search region above the bbox; never index a table without resolved column labels |
| 3 | Occasional cell merge/truncation | p16 t1: `$ 119,455 $ 106,` | Validate row width against modal width; flag and fall back |
| 4 | Units live outside the table | "(In millions…)", "(dollars in millions)" | Attach units to table metadata; surface in the prompt |
| 5 | No numbered captions | 0 "Table N:" occurrences | Use the preceding "The following table…" sentence + enclosing heading |
| 6 | PDF page ≠ printed page (offset 3) | Footer verified at 3 points | Store both; cite printed number, keep PDF index for retrieval |
| 7 | Repeated boilerplate | Running head + footer + notes line | Strip before chunking |
| 8 | No PDF outline | 0 TOC entries | Derive sections from bold-span heuristic |
| 9 | Duplicate content across Notes and MD&A | Segment data on pp. 16 and 19 | Metadata filtering + reranking; adversarial eval cases |
| 10 | Only one image, decorative | Apple logo, 46 × 56 px | Build a real figure pathway; report corpus honestly (§4) |
| 11 | Corpus is small (~70k chars) | Measured | Do not over-engineer for scale; justify retrieval on precision and citation |
| 12 | Encryption dict without password | RC4 flag, `needs_pass = 0` | Do not treat as a load failure |

---

## 9. Changes to the original plan warranted by these findings

The plan in `implementation_plan.md` was written before the PDF was inspected. Four of its
assumptions do not survive contact with the actual file:

1. **§8 and §27-C assume interrogable figures exist.** They do not. The pathway is built and
   demonstrated, but the requirement is satisfied architecturally and documented honestly
   rather than claimed as evidenced. See §4 above.
2. **§7 treats table flattening as the main risk.** The measured main risk is *header loss*
   (Defect 2), which flattening discussions do not address at all. Header recovery is
   promoted to the primary table objective.
3. **§4's inspection checklist asks about multi-column layouts, scanned pages, and
   footnotes.** All three are absent. That inspection effort is better spent on the EDGAR
   column-inflation problem, which the checklist did not anticipate.
4. **The plan is silent on page-number ambiguity.** With a constant offset of 3, this is a
   correctness issue affecting every citation the system produces, and it needs an explicit
   decision.

---

## 10. Open items

- Whether a table's structured representation should be stored as Markdown, JSON records, or
  both — decided in `design-decisions.md` after the normaliser is prototyped.
- Whether pdfplumber's table detector outperforms PyMuPDF's on the failure cases in Defect 3.
  Sampled on three pages only; **not yet measured across all 31 tables.**
- Retrieval configuration (chunk size, top-k, hybrid weighting) — to be set empirically
  against the evaluation set, not guessed.
