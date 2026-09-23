"""Source attribution carried by every retrievable unit of content.

Provenance is part of the data, not an afterthought (docs/architecture.md §1).
It is attached at extraction time and travels unchanged through chunking,
indexing, retrieval, and into the rendered citation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """Where a piece of content came from.

    The two page numbers are both required because they differ. In the source
    document the footer establishes that PDF page 4 is printed page 1 — an
    offset of three (docs/pdf-analysis.md §2). Citing the wrong one puts every
    reference three pages out.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(description="Stable document identifier, e.g. AAPL_2022_Q3_10Q.")
    pdf_page: int = Field(gt=0, description="1-based index into the PDF file.")
    printed_page: int | None = Field(
        default=None,
        description=(
            "Page number printed in the document footer. None on the cover, "
            "table of contents, and exhibit pages, which carry no footer."
        ),
    )

    part: str | None = Field(default=None, description='e.g. "Part I — Financial Information".')
    item: str | None = Field(default=None, description='e.g. "Item 2 — Management\'s Discussion".')
    section: str | None = Field(default=None, description='e.g. "Products and Services Performance".')
    note: str | None = Field(default=None, description='e.g. "Note 9 — Segment Information".')

    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="Source rectangle on the page, when applicable."
    )

    @property
    def citation(self) -> str:
        """Human-readable page citation.

        Printed page leads because that is what a reader sees on the page and
        what the table of contents uses; the PDF index follows for unambiguous
        lookup (docs/architecture.md §9).
        """
        if self.printed_page is not None:
            return f"Page {self.printed_page} (PDF p. {self.pdf_page})"
        return f"PDF p. {self.pdf_page}"

    @property
    def location(self) -> str:
        """Most specific section label available, for display alongside the citation."""
        return self.note or self.section or self.item or self.part or "Unattributed"
