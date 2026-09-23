"""Recover the document's section hierarchy from typography.

The PDF carries no outline — ``get_toc()`` returns zero entries
(docs/pdf-analysis.md §1) — so structure has to be read off the rendered page.
Inspection found a single font family at four sizes, with ``Arial-BoldMT`` at
8.0 pt and above marking every section and note heading. That rule reproduced
the full table of contents during Phase 2 (§7), which is why it is used here
rather than a layout model.

Headings are located with their vertical position so that a page carrying
several of them — page 14 has six — attributes each piece of content to the
heading actually above it, not merely to the last heading on the page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import pymupdf

# The dash character classes below deliberately include U+2014 EM DASH and
# U+2013 EN DASH. Part headings in this document are punctuated with an em dash
# and note headings with an en dash; a plain hyphen would match neither.
_PART = re.compile(r"^PART\s+([IVX]+)\b\s*[—–-]?\s*(.*)$")  # noqa: RUF001
_ITEM = re.compile(r"^Item\s+(\d+[A-Z]?)\.\s*(.*)$", re.IGNORECASE)
_NOTE = re.compile(r"^Note\s+(\d+)\s*[—–-]\s*(.+)$", re.IGNORECASE)  # noqa: RUF001


class HeadingKind(StrEnum):
    PART = "part"
    ITEM = "item"
    NOTE = "note"
    SUBSECTION = "subsection"


@dataclass(frozen=True)
class Heading:
    """A heading and where it sits."""

    text: str
    kind: HeadingKind
    pdf_page: int
    top: float
    """Vertical position of the heading's top edge, in PDF points from the page top."""

    size: float


@dataclass(frozen=True)
class SectionState:
    """The section context in force at a particular point in the document."""

    part: str | None = None
    item: str | None = None
    section: str | None = None
    note: str | None = None


def classify(text: str) -> HeadingKind:
    """Classify a heading by its wording."""
    if _PART.match(text):
        return HeadingKind.PART
    if _NOTE.match(text):
        return HeadingKind.NOTE
    if _ITEM.match(text):
        return HeadingKind.ITEM
    return HeadingKind.SUBSECTION


class SectionTagger:
    """Extracts headings and resolves the section context at any page position.

    Args:
        min_heading_size: Minimum font size, in points, for a bold line to count
            as a heading. The document's table labels are bold at 7.2 pt and
            must not be mistaken for section headings; body and headings sit at
            8.1 pt and above (docs/pdf-analysis.md §3).
        max_heading_words: Bold running text longer than this is prose, not a
            heading. Guards against an emphasised sentence being promoted.
    """

    def __init__(self, min_heading_size: float = 8.0, max_heading_words: int = 25) -> None:
        self.min_heading_size = min_heading_size
        self.max_heading_words = max_heading_words

    def extract_headings(
        self,
        doc: pymupdf.Document,
        is_boilerplate: Callable[[str], bool] | None = None,
    ) -> list[Heading]:
        """Find every heading in the document, in reading order.

        Args:
            doc: An open PDF.
            is_boilerplate: Optional predicate identifying page furniture. The
                running head is bold and would otherwise be read as a heading on
                every page it appears.
        """
        headings: list[Heading] = []

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue

                    text = "".join(span["text"] for span in spans).strip()
                    if not text or len(text.split()) > self.max_heading_words:
                        continue
                    if is_boilerplate is not None and is_boilerplate(text):
                        continue

                    if not all("bold" in span["font"].lower() for span in spans):
                        continue
                    size = max(span["size"] for span in spans)
                    if size < self.min_heading_size:
                        continue

                    headings.append(
                        Heading(
                            text=text,
                            kind=classify(text),
                            pdf_page=page_index + 1,
                            top=line["bbox"][1],
                            size=size,
                        )
                    )

        return headings

    def build_index(self, headings: list[Heading]) -> SectionIndex:
        """Turn a flat heading list into a positional lookup."""
        return SectionIndex(headings)


class SectionIndex:
    """Answers "which section is in force here?" for any page position.

    Section state is cumulative: a Part persists until the next Part, an Item
    until the next Item, and so on. A Note both sets the note and clears once a
    new Item begins, because notes belong to Item 1 and do not carry into MD&A.
    """

    def __init__(self, headings: list[Heading]) -> None:
        self._headings = sorted(headings, key=lambda h: (h.pdf_page, h.top))
        self._states = self._accumulate(self._headings)

    @staticmethod
    def _accumulate(headings: list[Heading]) -> list[SectionState]:
        states: list[SectionState] = []
        part = item = section = note = None

        for heading in headings:
            if heading.kind is HeadingKind.PART:
                part, item, section, note = heading.text, None, None, None
            elif heading.kind is HeadingKind.ITEM:
                item, section, note = heading.text, None, None
            elif heading.kind is HeadingKind.NOTE:
                note, section = heading.text, heading.text
            else:
                section = heading.text
            states.append(SectionState(part=part, item=item, section=section, note=note))

        return states

    def state_at(self, pdf_page: int, top: float | None = None) -> SectionState:
        """The section context in force at a position.

        Args:
            pdf_page: 1-based PDF page.
            top: Vertical offset on that page. When omitted, returns the state
                at the end of the page, which is the right answer for
                page-level content such as a page render.
        """
        position = (pdf_page, float("inf") if top is None else top)

        result = SectionState()
        for heading, state in zip(self._headings, self._states, strict=True):
            if (heading.pdf_page, heading.top) <= position:
                result = state
            else:
                break
        return result

    @property
    def headings(self) -> list[Heading]:
        return list(self._headings)
