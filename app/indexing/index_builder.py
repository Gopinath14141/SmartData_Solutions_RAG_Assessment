"""End-to-end ingestion: PDF in, searchable index out.

One place where the whole pipeline is wired together, so the CLI, the API, and
the tests all trigger identical work.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field

from app.chunking import build_chunks
from app.config import Settings, get_settings
from app.extraction import (
    PageRenderer,
    extract_figures,
    extract_tables,
    extract_text_blocks,
    table_regions,
)
from app.indexing.embedder import Embedder
from app.indexing.qdrant_store import QdrantStore
from app.ingestion import open_document
from app.models import ContentType, TableValidation

logger = logging.getLogger(__name__)


@dataclass
class IngestionReport:
    """What ingestion produced, and what it could not.

    Extraction failures are recorded rather than swallowed
    (docs/architecture.md §5). This report is what the assessment's measured
    numbers come from, so it must reflect what actually happened.
    """

    pages: int = 0
    text_chunks: int = 0
    table_chunks: int = 0
    figure_chunks: int = 0
    total_chunks: int = 0
    indexed: int = 0

    tables_ok: int = 0
    tables_flagged: dict[str, int] = field(default_factory=dict)
    raw_columns: int = 0
    collapsed_columns: int = 0
    captions_attached: int = 0
    units_attached: int = 0

    figures_total: int = 0
    figures_decorative: int = 0

    seconds_extract: float = 0.0
    seconds_embed: float = 0.0
    seconds_index: float = 0.0
    seconds_total: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def summary(self) -> str:
        flagged = ", ".join(f"{k}={v}" for k, v in self.tables_flagged.items()) or "none"
        return (
            f"{self.total_chunks} chunks indexed from {self.pages} pages "
            f"({self.text_chunks} text, {self.table_chunks} table, {self.figure_chunks} figure). "
            f"Tables: {self.tables_ok} OK, flagged: {flagged}. "
            f"Columns {self.raw_columns} -> {self.collapsed_columns}. "
            f"Total {self.seconds_total:.1f}s."
        )


def build_index(
    settings: Settings | None = None,
    rebuild: bool = False,
    embedder: Embedder | None = None,
    store: QdrantStore | None = None,
) -> IngestionReport:
    """Run the full pipeline and populate the index.

    Args:
        settings: Configuration override.
        rebuild: Drop and recreate the collection first.
        embedder: Injected for testing.
        store: Injected for testing.
    """
    settings = settings or get_settings()
    settings.ensure_directories()

    report = IngestionReport()
    started = time.perf_counter()

    # Fail on a missing key before doing minutes of extraction work.
    embedder = embedder or Embedder(settings)
    store = store or QdrantStore(settings)
    store.wait_until_ready()

    extract_started = time.perf_counter()
    with open_document(settings=settings) as loaded:
        renderer = PageRenderer(settings)
        tables = extract_tables(loaded)
        blocks = extract_text_blocks(loaded, table_regions(tables))
        figures = extract_figures(loaded, renderer, settings)
        renderer.render_all(loaded.doc)
        chunks = build_chunks(loaded, blocks, tables, figures, settings)

        report.pages = loaded.page_count

    report.seconds_extract = time.perf_counter() - extract_started

    for table in tables:
        payload = table.payload
        if payload.validation is TableValidation.OK:
            report.tables_ok += 1
        else:
            key = payload.validation.value
            report.tables_flagged[key] = report.tables_flagged.get(key, 0) + 1
        report.raw_columns += payload.raw_column_count
        report.collapsed_columns += len(payload.column_headers)
        report.captions_attached += 1 if payload.caption else 0
        report.units_attached += 1 if payload.units else 0

    report.figures_total = len(figures)
    report.figures_decorative = sum(1 for f in figures if f.payload.is_decorative)

    for chunk in chunks:
        if chunk.content_type is ContentType.TEXT:
            report.text_chunks += 1
        elif chunk.content_type is ContentType.TABLE:
            report.table_chunks += 1
        else:
            report.figure_chunks += 1
    report.total_chunks = len(chunks)

    embed_started = time.perf_counter()
    vectors = embedder.embed_texts([chunk.embed_text for chunk in chunks])
    report.seconds_embed = time.perf_counter() - embed_started

    index_started = time.perf_counter()
    store.create(recreate=rebuild)
    report.indexed = store.upsert(chunks, vectors)
    report.seconds_index = time.perf_counter() - index_started

    report.seconds_total = time.perf_counter() - started

    destination = settings.processed_dir / "ingestion_report.json"
    destination.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    logger.info("ingestion complete: %s", report.summary())

    return report
