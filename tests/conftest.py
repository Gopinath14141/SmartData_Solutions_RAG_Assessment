"""Shared fixtures.

Extraction is tested against the real PDF rather than a synthetic one, because
the document's defects *are* the specification. A hand-made fixture would have
clean headers and uninflated columns, and would therefore test none of the
behaviour that matters (docs/design-decisions.md D10).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.extraction import (
    ExtractedFigure,
    ExtractedTable,
    PageRenderer,
    TextBlock,
    extract_figures,
    extract_tables,
    extract_text_blocks,
    table_regions,
)
from app.ingestion import LoadedDocument, open_document
from app.models import Chunk, ContentType, Provenance

_MISSING_PDF = "Source PDF not present. Run `python scripts/download_document.py`."


def pytest_collection_modifyitems(config, items):
    """Skip document-backed tests when the PDF has not been downloaded."""
    if get_settings().resolved_document_path.exists():
        return
    skip = pytest.mark.skip(reason=_MISSING_PDF)
    for item in items:
        if "document" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def document() -> Iterator[LoadedDocument]:
    """The real filing, loaded once for the whole session."""
    with open_document() as loaded:
        yield loaded


@pytest.fixture(scope="session")
def tables(document: LoadedDocument) -> list[ExtractedTable]:
    return extract_tables(document)


@pytest.fixture(scope="session")
def text_blocks(document: LoadedDocument, tables: list[ExtractedTable]) -> list[TextBlock]:
    return extract_text_blocks(document, table_regions(tables))


@pytest.fixture(scope="session")
def figures(document: LoadedDocument) -> list[ExtractedFigure]:
    return extract_figures(document, PageRenderer())


@pytest.fixture
def provenance() -> Provenance:
    return Provenance(
        document_id="AAPL_2022_Q3_10Q",
        pdf_page=18,
        printed_page=15,
        part="PART I",
        item="Item 2",
        section="Products and Services Performance",
    )


@pytest.fixture
def text_chunk(provenance: Provenance) -> Chunk:
    return Chunk(
        chunk_id="AAPL_2022_Q3_10Q::p018::text::0",
        content_type=ContentType.TEXT,
        embed_text="net sales by category",
        display_text="Net sales by category increased.",
        provenance=provenance,
    )
