"""Open the source PDF and attach page-level structure to it.

Produces a :class:`LoadedDocument`: the open PDF plus, for every page, its
printed page number, its section context, and its text with page furniture
removed. Downstream extractors read structure from here rather than
re-deriving it, so the printed-page mapping and the section hierarchy are
computed once and are consistent across text, tables, and figures.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.config import Settings, get_settings
from app.errors import DocumentNotFoundError, EncryptedPDFError, InvalidPDFError
from app.ingestion.boilerplate import BoilerplateDetector, BoilerplateProfile
from app.ingestion.sections import SectionIndex, SectionTagger
from app.models import Provenance


@dataclass(frozen=True)
class PageInfo:
    """Everything known about one page before content extraction begins."""

    pdf_page: int
    printed_page: int | None
    lines: list[str]
    """Text lines with page furniture removed."""

    raw_line_count: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class LoadedDocument:
    """An open PDF together with its recovered structure.

    Holds a live :class:`pymupdf.Document`, so it is only valid inside the
    :func:`open_document` context manager.
    """

    doc: pymupdf.Document
    document_id: str
    pages: list[PageInfo]
    sections: SectionIndex
    boilerplate: BoilerplateProfile

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page(self, pdf_page: int) -> PageInfo:
        """Page info by 1-based PDF page number."""
        return self.pages[pdf_page - 1]

    def provenance(
        self,
        pdf_page: int,
        *,
        top: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> Provenance:
        """Build provenance for content at a position on a page.

        Single point of construction, so every extractor records the printed
        page and the section context the same way. ``top`` selects which
        heading applies when a page carries several.
        """
        info = self.page(pdf_page)
        state = self.sections.state_at(pdf_page, top if top is not None else (bbox[1] if bbox else None))
        return Provenance(
            document_id=self.document_id,
            pdf_page=pdf_page,
            printed_page=info.printed_page,
            part=state.part,
            item=state.item,
            section=state.section,
            note=state.note,
            bbox=bbox,
        )

    def page_offset(self) -> int | None:
        """The constant difference between PDF index and printed page, if there is one.

        Reported for diagnostics only — provenance always uses each page's own
        footer rather than assuming the offset holds. It is 3 in the source
        document, but that is a property of this filing, not of 10-Qs.
        """
        offsets = {
            info.pdf_page - info.printed_page
            for info in self.pages
            if info.printed_page is not None
        }
        return offsets.pop() if len(offsets) == 1 else None


def _validate(path: Path) -> pymupdf.Document:
    """Open a PDF, converting every failure mode into a typed error."""
    if not path.exists():
        raise DocumentNotFoundError(detail=str(path))

    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise InvalidPDFError(detail=f"{type(exc).__name__}: {exc}") from exc

    # `needs_pass` distinguishes a genuinely locked file from one that merely
    # carries an encryption dictionary. The source document is the latter — it
    # reports RC4 encryption yet opens without a password (pdf-analysis §1) —
    # so testing `is_encrypted` here would reject a valid file.
    if doc.needs_pass:
        doc.close()
        raise EncryptedPDFError(detail=str(path))

    if doc.page_count == 0:
        doc.close()
        raise InvalidPDFError("The PDF contains no pages.", detail=str(path))

    return doc


def _page_lines(doc: pymupdf.Document) -> list[list[str]]:
    """Non-empty, stripped text lines for every page, in order."""
    return [
        [line.strip() for line in doc.load_page(index).get_text("text").splitlines() if line.strip()]
        for index in range(doc.page_count)
    ]


@contextmanager
def open_document(
    path: Path | None = None,
    settings: Settings | None = None,
) -> Iterator[LoadedDocument]:
    """Open the source document and recover its structure.

    Args:
        path: Override for the configured document path.
        settings: Override for the global settings.

    Yields:
        A :class:`LoadedDocument`, valid until the context exits.
    """
    settings = settings or get_settings()
    target = path or settings.resolved_document_path

    doc = _validate(target)
    try:
        raw_lines = _page_lines(doc)

        detector = BoilerplateDetector()
        profile = detector.profile(raw_lines)

        tagger = SectionTagger()
        headings = tagger.extract_headings(doc, is_boilerplate=profile.is_boilerplate)
        index = tagger.build_index(headings)

        pages = [
            PageInfo(
                pdf_page=number,
                printed_page=detector.printed_page_number(lines, profile),
                lines=detector.strip(lines, profile),
                raw_line_count=len(lines),
            )
            for number, lines in enumerate(raw_lines, start=1)
        ]

        yield LoadedDocument(
            doc=doc,
            document_id=settings.document_id,
            pages=pages,
            sections=index,
            boilerplate=profile,
        )
    finally:
        doc.close()
