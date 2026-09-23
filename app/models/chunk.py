"""The single retrievable unit and its per-modality payloads.

One ``Chunk`` type with a discriminated payload, rather than three parallel
record types, keeps the index, retriever, and context builder generic while
letting each content type carry what it needs (docs/architecture.md §3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ContentType, TableParser, TableValidation
from app.models.provenance import Provenance


class TablePayload(BaseModel):
    """A normalised table: retrievable as text, interpretable as structure.

    Plan §7 requires both representations. ``markdown`` drives retrieval and
    display; ``rows`` gives the generation layer an unambiguous key lookup, so
    "net sales for the three months ended June 25, 2022" resolves to one cell
    rather than a guess among four columns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["table"] = "table"
    """Discriminator. Every field below has a default, so without an explicit tag a
    figure payload would deserialise into an empty table rather than failing.

    ``extra="forbid"`` closes the same hole from the other side: a payload tagged
    ``table`` but carrying figure fields is rejected instead of silently yielding a
    table with no rows."""

    caption: str | None = Field(
        default=None,
        description=(
            'The narrative sentence introducing the table, e.g. "The following table '
            'shows net sales by category…". This document uses no numbered captions; '
            "nine such sentences were located (docs/pdf-analysis.md §5)."
        ),
    )
    units: str | None = Field(
        default=None,
        description=(
            'Scale qualifier from the narrative above the table, e.g. "(In millions…)". '
            "Never inside the table itself. Losing it turns $82,959 million into $82,959."
        ),
    )

    column_headers: list[str] = Field(
        default_factory=list,
        description=(
            "Flattened column labels. Two-level headers join with a pipe, e.g. "
            '"Three Months Ended | June 25, 2022".'
        ),
    )
    rows: list[dict[str, str]] = Field(
        default_factory=list,
        description="Row label mapped to each column header's value.",
    )
    row_label_key: str = Field(
        default="label",
        description="Key under which each row's leading label is stored.",
    )

    markdown: str = Field(default="", description="Normalised table, ready to render.")

    parser: TableParser = TableParser.PYMUPDF
    validation: TableValidation = TableValidation.OK

    raw_column_count: int = Field(
        default=0,
        description=(
            "Column count before symbol/spacer collapse. Retained to quantify the "
            "EDGAR inflation described in Defect 1 — page 4 is 12 before, 5 after."
        ),
    )

    @property
    def is_trustworthy(self) -> bool:
        """Whether the structure can be relied on for precise value lookup."""
        return self.validation is TableValidation.OK


class FigurePayload(BaseModel):
    """An extracted image with the context needed to interpret it.

    The source document contains exactly one raster image — the Apple logo on
    the cover page (docs/pdf-analysis.md §4). The pathway is built properly
    regardless, and ``is_decorative`` keeps the system honest about what it has.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["figure"] = "figure"
    """Discriminator — see TablePayload.kind."""

    image_path: Path = Field(description="Extracted raster image on disk.")
    page_render_path: Path | None = Field(
        default=None,
        description="Full-page render, used for multimodal queries and table fallback.",
    )

    caption: str | None = Field(default=None, description="Nearest caption-like text, if any.")
    surrounding_text: str = Field(
        default="", description="Narrative immediately above and below the image."
    )

    width: int = Field(gt=0)
    height: int = Field(gt=0)

    is_decorative: bool = Field(
        default=False,
        description=(
            "True for logos, rules, and other non-informational images. Prevents the "
            "system from implying it has interpreted a data graphic when it has not."
        ),
    )


class Chunk(BaseModel):
    """One retrievable unit of the document."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(
        description='Stable identifier, e.g. "AAPL_2022_Q3_10Q::p18::table::0".'
    )
    content_type: ContentType

    embed_text: str = Field(
        description="What is embedded and BM25-indexed."
    )
    display_text: str = Field(
        description=(
            "What is shown to the model and the user. Kept separate from "
            "``embed_text`` because a table embeds best as caption plus a compact "
            "summary, but presents best as clean Markdown (docs/architecture.md §3)."
        )
    )

    provenance: Provenance
    payload: Annotated[TablePayload | FigurePayload, Field(discriminator="kind")] | None = Field(
        default=None, description="Type-specific detail; None for plain text chunks."
    )

    @property
    def table(self) -> TablePayload | None:
        """The table payload, or None if this chunk is not a table."""
        return self.payload if isinstance(self.payload, TablePayload) else None

    @property
    def figure(self) -> FigurePayload | None:
        """The figure payload, or None if this chunk is not a figure."""
        return self.payload if isinstance(self.payload, FigurePayload) else None
