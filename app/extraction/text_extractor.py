"""Extract narrative text, excluding anything already captured as a table.

Without the exclusion, every figure in every table would be indexed twice: once
inside a normalised table with its period headers attached, and once as loose
prose with no headers at all. The second copy is strictly worse and competes
with the first at retrieval time, so table regions are removed here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion import LoadedDocument

_OVERLAP_THRESHOLD = 0.5
"""A block more than half inside a table region is table content, not narrative."""


@dataclass(frozen=True)
class TextBlock:
    """A run of narrative text and its position."""

    text: str
    pdf_page: int
    bbox: tuple[float, float, float, float]

    @property
    def top(self) -> float:
        return self.bbox[1]


def _overlap_ratio(
    block: tuple[float, float, float, float],
    region: tuple[float, float, float, float],
) -> float:
    """Fraction of ``block``'s area that lies inside ``region``."""
    width = min(block[2], region[2]) - max(block[0], region[0])
    height = min(block[3], region[3]) - max(block[1], region[1])
    if width <= 0 or height <= 0:
        return 0.0

    block_area = (block[2] - block[0]) * (block[3] - block[1])
    return (width * height) / block_area if block_area else 0.0


def extract_text_blocks(
    loaded: LoadedDocument,
    regions: dict[int, list[tuple[float, float, float, float]]] | None = None,
) -> list[TextBlock]:
    """Extract narrative text blocks in reading order.

    Args:
        loaded: The loaded document.
        regions: Table bounding boxes per page, from
            :func:`app.extraction.table_extractor.table_regions`.
    """
    regions = regions or {}
    blocks: list[TextBlock] = []

    for pdf_page in range(1, loaded.page_count + 1):
        page = loaded.doc.load_page(pdf_page - 1)
        page_regions = regions.get(pdf_page, [])

        for block in page.get_text("dict")["blocks"]:
            lines = block.get("lines")
            if not lines:
                continue

            bbox = tuple(block["bbox"])
            if any(
                _overlap_ratio(bbox, region) > _OVERLAP_THRESHOLD for region in page_regions
            ):
                continue

            kept: list[str] = []
            for line in lines:
                text = "".join(span["text"] for span in line.get("spans", [])).strip()
                if text and not loaded.boilerplate.is_boilerplate(text):
                    kept.append(text)

            if not kept:
                continue

            blocks.append(
                TextBlock(text=" ".join(kept), pdf_page=pdf_page, bbox=bbox)
            )

    return blocks
