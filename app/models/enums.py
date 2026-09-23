"""Enumerations shared across the pipeline."""

from __future__ import annotations

from enum import StrEnum


class ContentType(StrEnum):
    """The three modalities the assignment requires the system to answer over.

    ``StrEnum`` so values serialise directly to JSON and can be used as Qdrant
    payload filter values without conversion.
    """

    TEXT = "text"
    TABLE = "table"
    FIGURE = "figure"


class TableValidation(StrEnum):
    """Outcome of validating an extracted table.

    Anything other than ``OK`` marks a table whose structure could not be fully
    trusted. These are recorded in the ingestion report rather than silently
    swallowed (docs/architecture.md §5, step 6).
    """

    OK = "ok"
    HEADERS_UNRESOLVED = "headers_unresolved"
    """Period/column headers could not be recovered from above the table bbox.

    The most damaging failure in this document: values retrieved without column
    labels are unattributable (docs/pdf-analysis.md §5, Defect 2).
    """

    WIDTH_MISMATCH = "width_mismatch"
    """At least one row's cell count disagrees with the table's modal width.

    Symptom of the cell-merge failure observed on page 16 (Defect 3).
    """

    PARSE_FAILED = "parse_failed"
    """Both PyMuPDF and pdfplumber failed; the table routes to page-render fallback."""


class TableParser(StrEnum):
    """Which library produced a table, for provenance and for measuring fallback rate."""

    PYMUPDF = "pymupdf"
    PDFPLUMBER = "pdfplumber"
