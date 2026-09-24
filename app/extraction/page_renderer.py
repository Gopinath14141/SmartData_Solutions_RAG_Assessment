"""Render pages to images.

Two consumers, both load-bearing:

* The figure pathway, where a multimodal model reads a page visually.
* The table fallback, for tables that fail validation. When structure cannot be
  trusted, the rendered page still shows the filing exactly as published, which
  is better evidence than a mis-parsed grid.

Renders are cached on disk, since ingestion and evaluation both re-run often and
rasterising 28 pages is the slowest part of the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.config import Settings, get_settings
from app.errors import PageRenderError


class PageRenderer:
    """Renders and caches page images."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.renders_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, pdf_page: int) -> Path:
        return self.settings.renders_dir / f"page_{pdf_page:03d}.png"

    def render(self, doc: pymupdf.Document, pdf_page: int, force: bool = False) -> Path:
        """Render one page, returning the cached file when it already exists.

        Args:
            doc: The open document.
            pdf_page: 1-based page number.
            force: Re-render even if a cached image is present.
        """
        destination = self.path_for(pdf_page)
        if destination.exists() and not force:
            return destination

        try:
            page = doc.load_page(pdf_page - 1)
            pixmap = page.get_pixmap(dpi=self.settings.page_render_dpi)
            pixmap.save(destination)
        except Exception as exc:
            raise PageRenderError(detail=f"page {pdf_page}: {type(exc).__name__}: {exc}") from exc

        return destination

    def render_all(self, doc: pymupdf.Document, force: bool = False) -> dict[int, Path]:
        """Render every page."""
        return {
            number: self.render(doc, number, force=force)
            for number in range(1, doc.page_count + 1)
        }
