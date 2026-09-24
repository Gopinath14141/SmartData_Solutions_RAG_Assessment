"""Extraction of text, tables, and figures from the loaded document."""

from app.extraction.figure_extractor import ExtractedFigure, extract_figures
from app.extraction.page_renderer import PageRenderer
from app.extraction.table_extractor import ExtractedTable, extract_tables, table_regions
from app.extraction.table_headers import HeaderColumn, assign_to_columns, recover_headers
from app.extraction.table_normaliser import (
    collapse,
    extract_context,
    normalise_table,
    to_markdown,
    validate,
)
from app.extraction.text_extractor import TextBlock, extract_text_blocks

__all__ = [
    "ExtractedFigure",
    "ExtractedTable",
    "HeaderColumn",
    "PageRenderer",
    "TextBlock",
    "assign_to_columns",
    "collapse",
    "extract_context",
    "extract_figures",
    "extract_tables",
    "extract_text_blocks",
    "normalise_table",
    "recover_headers",
    "table_regions",
    "to_markdown",
    "validate",
]
