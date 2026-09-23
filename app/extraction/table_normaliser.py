"""Turn a detected table into a representation that can be retrieved and read.

The EDGAR HTML-to-PDF conversion emits every table cell as an independently
positioned run, including currency symbols and percent signs. A table that is
logically five columns wide extracts as twelve; page 18's six columns extract as
eighteen. Roughly 60% of extracted cells are empty or hold a bare symbol
(docs/pdf-analysis.md §5, Defect 1).

This module collapses that back to the logical shape, attaches the caption and
units that live in the narrative above the table rather than inside it, and
validates the result so that tables the parser mangled are marked instead of
being indexed as though they were sound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import pymupdf

from app.extraction.table_headers import assign_to_columns, recover_headers
from app.models import TableParser, TablePayload, TableValidation

_CONTEXT_BAND = 170.0
"""How far above a table to look for its caption and units, in points."""

_UNITS = re.compile(
    r"\(\s*(?:in|dollars in|amounts in)\b[^)]*\)",
    re.IGNORECASE,
)
# Caption sentences in this filing end in a colon far more often than a full
# stop -- "...for the three- and nine-month periods ended June 25, 2022 and
# June 26, 2021 (dollars in millions):" -- so both terminators are accepted, and
# the match starts at the templated opening rather than at the preceding heading.
_CAPTION = re.compile(r"\bThe following tables?\b[^.:]*[.:]", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PARENTHESISED = re.compile(r"^\(([\d,]+(?:\.\d+)?)\)$")
_WHITESPACE = re.compile(r"\s+")


class ColumnKind(StrEnum):
    """What a raw extracted column actually contains."""

    EMPTY = "empty"
    """No content on any row — a spacer emitted by the HTML conversion."""

    CURRENCY = "currency"
    """Only currency symbols. Belongs to the value column on its right."""

    PERCENT = "percent"
    """Only percent signs. Belongs to the value column on its left."""

    DATA = "data"


@dataclass(frozen=True)
class NormalisedTable:
    """A table reduced to its logical shape, with provenance of the reduction."""

    headers: list[str]
    rows: list[list[str]]
    raw_column_count: int
    validation: TableValidation


def column_spans(table: pymupdf.table.Table) -> list[tuple[float, float]]:
    """Horizontal extent of each column, unioned over every row."""
    spans: dict[int, tuple[float, float]] = {}
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            if cell is None:
                continue
            low, high = spans.get(index, (cell[0], cell[2]))
            spans[index] = (min(low, cell[0]), max(high, cell[2]))
    return [spans[index] for index in sorted(spans)]


def classify_column(cells: list[str]) -> ColumnKind:
    """Decide what a raw column holds, from its non-empty cells."""
    values = [cell.strip() for cell in cells if cell and cell.strip()]
    if not values:
        return ColumnKind.EMPTY
    if all(value == "$" for value in values):
        return ColumnKind.CURRENCY
    if all(value == "%" for value in values):
        return ColumnKind.PERCENT
    return ColumnKind.DATA


def normalise_value(value: str, currency: str = "", percent: str = "") -> str:
    """Clean a single cell and re-attach any symbol that had its own column.

    Parenthesised negatives are converted to a leading minus. Financial
    statements write ``(10)`` for minus ten, and leaving that convention in
    place invites a sign error the moment a model does arithmetic on it.
    """
    text = _WHITESPACE.sub(" ", (value or "").strip())
    if not text:
        return ""

    negative = _PARENTHESISED.match(text)
    if negative:
        text = f"-{negative.group(1)}"

    return f"{currency}{text}{percent}"


def collapse(
    labels: list[str],
    raw_rows: list[list[str | None]],
) -> tuple[list[str], list[list[str]]]:
    """Reduce symbol and spacer columns into the values they belong to.

    ``| Products | $ | 63,355 | | $ | 63,948 |`` becomes
    ``| Products | $63,355 | $63,948 |``.

    Args:
        labels: One header label per raw column.
        raw_rows: Extracted rows, each with one entry per raw column.

    Returns:
        Collapsed headers and rows. The leading row-label column is preserved
        as the first column.
    """
    if not raw_rows:
        return [], []

    width = max(len(row) for row in raw_rows)
    padded = [list(row) + [None] * (width - len(row)) for row in raw_rows]
    labels = list(labels) + [""] * (width - len(labels))

    kinds = [
        classify_column([str(row[index] or "") for row in padded])
        for index in range(width)
    ]

    # Raw columns that were assigned the same header belong to one logical
    # column. This is what makes the staggered layout tractable: the conversion
    # puts a value in a different raw column depending on whether a currency
    # symbol precedes it, so on page 18 raw column 1 holds "$" for iPhone but
    # "7,382" for Mac, while raw column 2 holds "40,665" for iPhone and nothing
    # for Mac. Neither column is uniformly a symbol column, and neither alone
    # holds the period's values — but together, per row, they hold exactly one.
    groups: list[tuple[str, list[int]]] = []
    for index in range(width):
        if kinds[index] is not ColumnKind.DATA:
            continue
        label = labels[index]
        if groups and label and groups[-1][0] == label:
            groups[-1][1].append(index)
        else:
            groups.append((label, [index]))

    if not groups:
        return [], []

    # Symbol-only columns have no header of their own and attach to a neighbour:
    # currency to the group on its right, percent to the group on its left.
    extra: dict[int, list[int]] = {}
    for index, kind in enumerate(kinds):
        if kind is ColumnKind.CURRENCY:
            target = next(
                (gi for gi, (_, cols) in enumerate(groups) if min(cols) > index), None
            )
        elif kind is ColumnKind.PERCENT:
            target = next(
                (gi for gi in range(len(groups) - 1, -1, -1) if max(groups[gi][1]) < index),
                None,
            )
        else:
            continue
        if target is not None:
            extra.setdefault(target, []).append(index)

    collapsed_rows: list[list[str]] = []
    for row in padded:
        collapsed: list[str] = []
        for group_index, (_, columns) in enumerate(groups):
            cells = [
                str(row[index] or "").strip()
                for index in columns + extra.get(group_index, [])
            ]
            currency = "$" if "$" in cells else ""
            percent = "%" if "%" in cells else ""
            values = [cell for cell in cells if cell and cell not in ("$", "%")]
            # More than one value in a group means the parser merged cells;
            # joining keeps the evidence so validate() can flag it rather than
            # silently discarding a number.
            collapsed.append(normalise_value(" ".join(values), currency, percent))

        if any(cell for cell in collapsed):
            collapsed_rows.append(collapsed)

    return [label for label, _ in groups], collapsed_rows


def extract_context(
    page: pymupdf.Page,
    table_bbox: tuple[float, float, float, float],
    floor_y: float | None = None,
) -> tuple[str | None, str | None]:
    """Find the caption sentence and units qualifier above a table.

    This document uses no numbered captions. Tables are introduced by a
    near-templated sentence — ``The following table shows net sales by
    category…`` — and the scale lives in a separate parenthetical such as
    ``(In millions…)``. Together they carry exactly what the extraction
    destroys: what the table contains, which periods it covers, and the units
    (docs/pdf-analysis.md §5).

    Returns:
        ``(caption, units)``, either of which may be None.
    """
    x0, y0, x1, _ = table_bbox
    top = max(0.0, y0 - _CONTEXT_BAND)
    if floor_y is not None:
        top = max(top, floor_y)

    clip = pymupdf.Rect(x0 - 5, top, x1 + 5, y0 - 1)
    lines: list[str] = []
    for block in page.get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            if line["bbox"][1] < top:
                continue
            text = "".join(span["text"] for span in line.get("spans", [])).strip()
            if text:
                lines.append(text)

    joined = _WHITESPACE.sub(" ", " ".join(lines))

    caption_matches = _CAPTION.findall(joined)
    caption = caption_matches[-1].strip() if caption_matches else None

    units_matches = _UNITS.findall(joined)
    units = units_matches[-1].strip() if units_matches else None

    return caption, units


def validate(headers: list[str], rows: list[list[str]]) -> TableValidation:
    """Check that the collapsed table can be trusted for value lookup.

    Two failures matter. Unresolved headers make every value unattributable to a
    period. A cell holding more than one number means the parser merged
    neighbouring cells — page 16's ``$ 119,455 $ 106,`` is the observed case, and
    it also truncates a value (Defect 3).
    """
    if not any(header for header in headers[1:]):
        return TableValidation.HEADERS_UNRESOLVED

    for row in rows:
        for cell in row[1:]:
            if len(_NUMBER.findall(cell)) > 1:
                return TableValidation.WIDTH_MISMATCH

    return TableValidation.OK


def to_markdown(headers: list[str], rows: list[list[str]]) -> str:
    """Render the collapsed table as a Markdown table."""
    if not headers:
        return ""

    def escape(cell: str) -> str:
        return cell.replace("|", r"\|")

    display = [escape(h) if h else " " for h in headers]
    out = ["| " + " | ".join(display) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        padded = list(row) + [""] * (len(headers) - len(row))
        out.append("| " + " | ".join(escape(cell) for cell in padded[: len(headers)]) + " |")
    return "\n".join(out)


def normalise_table(
    page: pymupdf.Page,
    table: pymupdf.table.Table,
    floor_y: float | None = None,
    parser: TableParser = TableParser.PYMUPDF,
) -> TablePayload:
    """Normalise one detected table into a :class:`TablePayload`.

    Args:
        page: The page the table sits on.
        table: A table from ``page.find_tables()``.
        floor_y: Bottom edge of the preceding table on this page, if any.
        parser: Which library produced this table.
    """
    bbox = tuple(table.bbox)
    raw_rows = table.extract()
    raw_column_count = max((len(row) for row in raw_rows), default=0)

    headers = recover_headers(page, bbox, floor_y=floor_y)
    labels = assign_to_columns(headers, column_spans(table), bbox[0])

    collapsed_headers, collapsed_rows = collapse(labels, raw_rows)
    caption, units = extract_context(page, bbox, floor_y=floor_y)
    validation = validate(collapsed_headers, collapsed_rows)

    row_label_key = "label"
    records: list[dict[str, str]] = []
    for row in collapsed_rows:
        record = {row_label_key: row[0] if row else ""}
        for index, header in enumerate(collapsed_headers[1:], start=1):
            if index < len(row):
                record[header or f"column_{index}"] = row[index]
        records.append(record)

    return TablePayload(
        caption=caption,
        units=units,
        column_headers=collapsed_headers,
        rows=records,
        row_label_key=row_label_key,
        markdown=to_markdown(collapsed_headers, collapsed_rows),
        parser=parser,
        validation=validation,
        raw_column_count=raw_column_count,
    )
