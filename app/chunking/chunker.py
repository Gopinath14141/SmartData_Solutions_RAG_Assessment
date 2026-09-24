"""Assemble extracted content into retrievable chunks.

Chunking is per content type, because one strategy cannot serve all three
(docs/architecture.md §6):

* **Text** is packed to a target size along section boundaries. Uniform
  N-character splitting is rejected — it severs arguments mid-sentence and
  produces chunks that cite the wrong heading.
* **Tables are never split.** The largest is 28 rows, and splitting one severs
  its rows from the period headers that were so expensive to recover. That
  would reintroduce the document's worst defect by hand.
* **Figures** are chunked as their surrounding narrative, since the image
  itself is not embeddable by a text model.

Every chunk carries two texts. ``embed_text`` is written for retrieval and is
prefixed with section context, so a query naming a section can match a chunk
whose body never repeats the heading. ``display_text`` is what the model and
the user actually read.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.extraction import ExtractedFigure, ExtractedTable, TextBlock
from app.ingestion import LoadedDocument
from app.models import Chunk, ContentType, Provenance

_OVERLAP_SEPARATORS = (". ", "; ", ", ", " ")


def _context_prefix(provenance: Provenance) -> str:
    """Section breadcrumb prepended to embedded text."""
    parts = [
        part
        for part in (provenance.part, provenance.item, provenance.note, provenance.section)
        if part
    ]
    # De-duplicate while preserving order: a note sets both `note` and `section`.
    seen: set[str] = set()
    unique = [p for p in parts if not (p in seen or seen.add(p))]
    return " > ".join(unique)


def _tail(text: str, size: int) -> str:
    """Trailing ``size`` characters, cut at a sentence or clause boundary."""
    if size <= 0 or len(text) <= size:
        return text
    window = text[-size:]
    for separator in _OVERLAP_SEPARATORS:
        position = window.find(separator)
        if position != -1:
            return window[position + len(separator) :]
    return window


def chunk_text_blocks(
    loaded: LoadedDocument,
    blocks: list[TextBlock],
    settings: Settings | None = None,
) -> list[Chunk]:
    """Pack narrative blocks into section-aware chunks."""
    settings = settings or get_settings()
    size = settings.text_chunk_size
    overlap = settings.text_chunk_overlap

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_length = 0
    anchor: TextBlock | None = None
    anchor_key: tuple[int, str] | None = None
    counter: dict[int, int] = {}

    def flush() -> None:
        nonlocal buffer, buffer_length, anchor, anchor_key
        if not buffer or anchor is None:
            buffer, buffer_length, anchor, anchor_key = [], 0, None, None
            return

        body = " ".join(buffer).strip()
        if len(body) >= settings.min_text_chunk_chars:
            provenance = loaded.provenance(anchor.pdf_page, top=anchor.top)
            index = counter.get(anchor.pdf_page, 0)
            counter[anchor.pdf_page] = index + 1
            prefix = _context_prefix(provenance)
            chunks.append(
                Chunk(
                    chunk_id=f"{loaded.document_id}::p{anchor.pdf_page:03d}::text::{index}",
                    content_type=ContentType.TEXT,
                    embed_text=f"{prefix}\n\n{body}" if prefix else body,
                    display_text=body,
                    provenance=provenance,
                )
            )

        buffer, buffer_length, anchor, anchor_key = [], 0, None, None

    for block in sorted(blocks, key=lambda b: (b.pdf_page, b.top)):
        state = loaded.sections.state_at(block.pdf_page, block.top)
        key = (block.pdf_page, state.section or state.item or "")

        # A section or page change is a real semantic boundary; do not pack across it.
        if anchor_key is not None and key != anchor_key:
            flush()

        if buffer and buffer_length + len(block.text) > size:
            carried = _tail(" ".join(buffer), overlap)
            flush()
            if carried:
                buffer, buffer_length = [carried], len(carried)

        if anchor is None:
            anchor, anchor_key = block, key
        buffer.append(block.text)
        buffer_length += len(block.text) + 1

    flush()
    return chunks


def chunk_tables(loaded: LoadedDocument, tables: list[ExtractedTable]) -> list[Chunk]:
    """One chunk per table, never split."""
    chunks: list[Chunk] = []

    for table in tables:
        payload = table.payload
        provenance = table.provenance
        prefix = _context_prefix(provenance)

        # Embedding text names the table's subject, its periods, and its row
        # labels. Numbers alone embed poorly; the labels are what a question
        # actually resembles.
        row_labels = ", ".join(
            record.get(payload.row_label_key, "")
            for record in payload.rows
            if record.get(payload.row_label_key)
        )
        embed_parts = [
            prefix,
            payload.caption or "",
            payload.units or "",
            " | ".join(header for header in payload.column_headers if header),
            row_labels,
        ]

        # The caption frequently ends with the units parenthetical already, so
        # only add it separately when it is genuinely missing.
        caption = payload.caption or ""
        units = payload.units or ""
        display_parts = [
            caption,
            "" if units and units in caption else units,
            payload.markdown,
        ]
        if table.needs_visual_fallback:
            display_parts.append(
                f"[Structure unreliable: {payload.validation.value}. "
                f"Refer to the page image for {provenance.citation}.]"
            )

        chunks.append(
            Chunk(
                chunk_id=(
                    f"{loaded.document_id}::p{provenance.pdf_page:03d}"
                    f"::table::{table.index_on_page}"
                ),
                content_type=ContentType.TABLE,
                embed_text="\n".join(part for part in embed_parts if part),
                display_text="\n\n".join(part for part in display_parts if part),
                provenance=provenance,
                payload=payload,
            )
        )

    return chunks


def _figure_inventory_chunk(
    loaded: LoadedDocument, figures: list[ExtractedFigure]
) -> Chunk:
    """A document-level summary of what imagery the filing contains.

    "What figures are in this document?" is a question about the corpus, not
    about any one figure, and no per-figure chunk can answer it. Without this
    chunk the question retrieves unrelated prose and invites the model to
    describe something that is not there — observed in testing, where it
    reported the exhibit certifications as images.

    The content is derived from measured extraction, not asserted: the counts
    and the decorative flags come from what the extractor actually found.
    """
    total = len(figures)
    decorative = sum(1 for figure in figures if figure.payload.is_decorative)
    informational = total - decorative

    def describe_page(pdf_page: int) -> str:
        printed = loaded.page(pdf_page).printed_page
        return f"page {printed}" if printed is not None else f"PDF page {pdf_page}"

    pages = sorted({figure.provenance.pdf_page for figure in figures})
    where = ", ".join(describe_page(page) for page in pages) or "none"

    if total == 0:
        body = (
            f"This document contains no embedded images, figures, charts, graphs, "
            f"diagrams, or illustrations across its {loaded.page_count} pages."
        )
    else:
        body = (
            f"Inventory of images and figures in this document. The filing contains "
            f"{total} embedded image{'s' if total != 1 else ''} across "
            f"{loaded.page_count} pages, located on {where}. "
            f"{decorative} of these {'is' if decorative == 1 else 'are'} decorative "
            f"(a logo or rule) rather than informational."
        )
        if informational == 0:
            body += (
                " The document contains no charts, graphs, diagrams, or data figures — "
                "nothing with values to read off. It is a text and tables document."
            )

    anchor = pages[0] if pages else 1
    return Chunk(
        chunk_id=f"{loaded.document_id}::document::figure-inventory",
        content_type=ContentType.FIGURE,
        embed_text=body,
        display_text=body,
        # Provenance is built directly rather than through
        # LoadedDocument.provenance(), which would attach whichever section
        # heading happens to precede the anchor page. This chunk summarises the
        # whole document and belongs to no section; inheriting one would put a
        # misleading heading on its citation.
        provenance=Provenance(
            document_id=loaded.document_id,
            pdf_page=anchor,
            printed_page=loaded.page(anchor).printed_page,
            section="Document summary",
        ),
    )


def chunk_figures(loaded: LoadedDocument, figures: list[ExtractedFigure]) -> list[Chunk]:
    """One chunk per figure, plus a document-level inventory chunk."""
    chunks: list[Chunk] = [_figure_inventory_chunk(loaded, figures)]

    for index, figure in enumerate(figures):
        payload = figure.payload
        provenance = figure.provenance
        prefix = _context_prefix(provenance)

        kind = "Decorative image" if payload.is_decorative else "Figure"
        descriptor = (
            f"{kind} on {provenance.citation}, {payload.width}x{payload.height} pixels."
        )

        # The descriptor leads, and surrounding text is capped. Embedding the
        # full page context first buried the fact that this chunk *is* an image
        # under unrelated cover-page prose, and the chunk stopped being
        # retrievable by a question about images at all.
        context = payload.surrounding_text[:400]
        embed_parts = [
            f"Image / figure. {descriptor}",
            payload.caption or "",
            prefix,
            context,
        ]
        display_parts = [payload.caption or "", descriptor, payload.surrounding_text]

        chunks.append(
            Chunk(
                chunk_id=f"{loaded.document_id}::p{provenance.pdf_page:03d}::figure::{index}",
                content_type=ContentType.FIGURE,
                embed_text="\n".join(part for part in embed_parts if part),
                display_text="\n\n".join(part for part in display_parts if part),
                provenance=provenance,
                payload=payload,
            )
        )

    return chunks


def build_chunks(
    loaded: LoadedDocument,
    blocks: list[TextBlock],
    tables: list[ExtractedTable],
    figures: list[ExtractedFigure],
    settings: Settings | None = None,
) -> list[Chunk]:
    """Build the full chunk set for a document, in reading order."""
    chunks = (
        chunk_text_blocks(loaded, blocks, settings)
        + chunk_tables(loaded, tables)
        + chunk_figures(loaded, figures)
    )
    return sorted(chunks, key=lambda c: (c.provenance.pdf_page, c.content_type.value))
