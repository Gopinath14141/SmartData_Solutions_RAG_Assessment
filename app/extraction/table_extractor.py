"""Locate tables on each page and normalise them.

Thin orchestration over :mod:`app.extraction.table_normaliser`. Its one real
responsibility is ordering: a table's header band is bounded below by the
preceding table on the same page, so tables must be processed in reading order
with each one's floor derived from the last.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.extraction.table_normaliser import normalise_table
from app.ingestion import LoadedDocument
from app.models import Provenance, TablePayload, TableValidation


@dataclass(frozen=True)
class ExtractedTable:
    """A normalised table and where it came from."""

    payload: TablePayload
    provenance: Provenance
    index_on_page: int

    @property
    def needs_visual_fallback(self) -> bool:
        """Whether this table's structure is too unreliable to answer from directly.

        Such tables are still indexed — their caption and row labels remain
        useful for retrieval — but the generation layer is told to read the page
        render rather than trust the parsed grid.
        """
        return self.payload.validation is not TableValidation.OK


def extract_tables(loaded: LoadedDocument) -> list[ExtractedTable]:
    """Extract and normalise every table in the document, in reading order."""
    tables: list[ExtractedTable] = []

    for pdf_page in range(1, loaded.page_count + 1):
        page = loaded.doc.load_page(pdf_page - 1)
        found = sorted(page.find_tables(), key=lambda t: t.bbox[1])

        for index, table in enumerate(found):
            # Bound the header search below by the previous table on this page.
            floor = max(
                (other.bbox[3] for other in found if other.bbox[3] <= table.bbox[1]),
                default=None,
            )
            payload = normalise_table(page, table, floor_y=floor)
            tables.append(
                ExtractedTable(
                    payload=payload,
                    provenance=loaded.provenance(pdf_page, bbox=tuple(table.bbox)),
                    index_on_page=index,
                )
            )

    return tables


def table_regions(tables: list[ExtractedTable]) -> dict[int, list[tuple[float, float, float, float]]]:
    """Table bounding boxes grouped by page.

    Used by the text extractor to avoid emitting table contents a second time as
    narrative text.
    """
    regions: dict[int, list[tuple[float, float, float, float]]] = {}
    for table in tables:
        if table.provenance.bbox is not None:
            regions.setdefault(table.provenance.pdf_page, []).append(table.provenance.bbox)
    return regions
