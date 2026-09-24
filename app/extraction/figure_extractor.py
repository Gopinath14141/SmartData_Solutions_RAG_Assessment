"""Extract embedded images with the context needed to interpret them.

The assignment requires answering questions about figures. This document
contains exactly one raster image across 28 pages, and it is the Apple logo
(docs/pdf-analysis.md §4). The pathway is therefore built properly and reported
honestly rather than quietly redefined to mean "tables".

``is_decorative`` is the mechanism for that honesty: an uncaptioned image small
enough to be a logo or a rule is marked, so the system never implies it has
interpreted a data graphic that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from app.config import Settings, get_settings
from app.errors import FigureExtractionError
from app.extraction.page_renderer import PageRenderer
from app.ingestion import LoadedDocument
from app.models import FigurePayload, Provenance


@dataclass(frozen=True)
class ExtractedFigure:
    """A figure and where it came from."""

    payload: FigurePayload
    provenance: Provenance


def _context_text(
    page: pymupdf.Page,
    bbox: tuple[float, float, float, float],
    band: float,
) -> str:
    """Narrative immediately above and below an image."""
    above = pymupdf.Rect(0, max(0.0, bbox[1] - band), page.rect.width, bbox[1])
    below = pymupdf.Rect(0, bbox[3], page.rect.width, bbox[3] + band)

    parts: list[str] = []
    for clip in (above, below):
        text = page.get_text("text", clip=clip).strip()
        if text:
            parts.append(" ".join(text.split()))
    return "\n".join(parts)


def extract_figures(
    loaded: LoadedDocument,
    renderer: PageRenderer | None = None,
    settings: Settings | None = None,
) -> list[ExtractedFigure]:
    """Extract every embedded image, with provenance and context.

    Images smaller than ``figure_min_dimension`` in both directions are skipped
    outright — those are rules and bullets, not figures.
    """
    settings = settings or get_settings()
    renderer = renderer or PageRenderer(settings)
    settings.figures_dir.mkdir(parents=True, exist_ok=True)

    figures: list[ExtractedFigure] = []

    for pdf_page in range(1, loaded.page_count + 1):
        page = loaded.doc.load_page(pdf_page - 1)

        for index, image in enumerate(page.get_images(full=True)):
            xref = image[0]
            try:
                meta = loaded.doc.extract_image(xref)
            except Exception as exc:
                raise FigureExtractionError(
                    detail=f"page {pdf_page} xref {xref}: {type(exc).__name__}: {exc}"
                ) from exc

            width, height = meta["width"], meta["height"]
            if width < settings.figure_min_dimension and height < settings.figure_min_dimension:
                continue

            rects = page.get_image_rects(xref)
            bbox = tuple(rects[0]) if rects else None

            image_path = settings.figures_dir / f"p{pdf_page:03d}_img{index}.{meta['ext']}"
            image_path.write_bytes(meta["image"])

            context = (
                _context_text(page, bbox, settings.figure_context_band) if bbox else ""
            )
            # This document has no numbered figure captions at all, so a caption
            # is only claimed when the surrounding text actually names a figure.
            caption = next(
                (
                    line
                    for line in context.splitlines()
                    if line.lower().startswith(("figure", "chart", "exhibit", "graph"))
                ),
                None,
            )

            decorative = caption is None and max(width, height) <= (
                settings.figure_decorative_max_dimension
            )

            figures.append(
                ExtractedFigure(
                    payload=FigurePayload(
                        image_path=image_path,
                        page_render_path=renderer.render(loaded.doc, pdf_page),
                        caption=caption,
                        surrounding_text=context,
                        width=width,
                        height=height,
                        is_decorative=decorative,
                    ),
                    provenance=loaded.provenance(pdf_page, bbox=bbox),
                )
            )

    return figures
