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

    COLUMNS_AMBIGUOUS = "columns_ambiguous"
    """Two columns resolved to the same header, so values cannot be attributed.

    Raised when the table's cell geometry is corrupt enough that header
    assignment produces duplicates or an impossible ordering. Page 16's second
    table does this: the same period resolves to two different columns, and its
    row labels absorb values. An automated repair was attempted and removed —
    it produced a table that validated cleanly while holding wrong numbers,
    which is worse than declaring the table untrustworthy.
    """

    PARSE_FAILED = "parse_failed"
    """Both PyMuPDF and pdfplumber failed; the table routes to page-render fallback."""


class TableParser(StrEnum):
    """Which library produced a table, for provenance and for measuring fallback rate."""

    PYMUPDF = "pymupdf"
    PDFPLUMBER = "pdfplumber"
