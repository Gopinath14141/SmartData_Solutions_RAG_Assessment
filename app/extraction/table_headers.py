"""Recover the column headers that sit outside a detected table.

This is the single most consequential piece of extraction in the project.

PyMuPDF places the table bounding box around the data rows, leaving the period
header band above it. Its own ``Table.header`` was measured against this
document and is not usable: on pages 4 and 16 it reports ``external=False`` and
returns the first *data* row as the header (``['Products', '$', '63,355', …]``),
and on page 16's second table it merges two dates into one cell. Three of four
sampled tables were wrong.

Without repair, a value like ``63,355`` is indexed with no indication of which
of four periods it belongs to, and the system answers period-specific questions
by guessing a column (docs/pdf-analysis.md §5, Defect 2).

The band above the table has structure that has to be read rather than
flattened:

* Headers are split across lines — ``June 25,`` on one line, ``2022`` on the
  next, in the same column.
* A group header such as ``Three Months Ended`` is *centred over* its columns,
  so its own x-range does not span them. On page 18 it covers only one of the
  three columns it heads.
* The caption sentence and the units line live in the same band and must not be
  mistaken for headers.

The approach: collect text fragments in the band, drop caption-width lines,
group fragments into columns by x-overlap, merge each column's fragments top to
bottom, then assign group labels by midpoint boundaries between consecutive
group headers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

_Y_TOLERANCE = 3.0
"""Fragments within this many points vertically are treated as one row."""

_DEFAULT_BAND = 70.0
"""How far above the table to look for headers, in points."""

_MAX_CAPTION_WIDTH_RATIO = 0.35
"""A fragment wider than this fraction of the table is prose, not a header cell."""

_LABEL_COLUMN_TOLERANCE = 6.0
"""A column starting within this distance of the table's left edge is the row-label column."""


@dataclass(frozen=True)
class Fragment:
    """A run of text in the header band."""

    text: str
    x0: float
    x1: float
    y: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2

    def overlaps(self, other: Fragment) -> bool:
        return self.x0 < other.x1 and other.x0 < self.x1


@dataclass(frozen=True)
class HeaderColumn:
    """A recovered leaf header and the horizontal span it occupies."""

    label: str
    x0: float
    x1: float
    group: str | None = None

    @property
    def full_label(self) -> str:
        """Group and leaf joined, e.g. ``Three Months Ended | June 25, 2022``."""
        return f"{self.group} | {self.label}" if self.group else self.label

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


def _collect_fragments(
    page: pymupdf.Page,
    table_bbox: tuple[float, float, float, float],
    band: float,
    floor_y: float | None,
) -> list[Fragment]:
    """Text fragments in the band directly above a table.

    Args:
        floor_y: Hard upper limit — normally the bottom of the preceding table
            on the page. A table's headers always lie between the previous table
            and this one, so searching past that boundary can only pick up the
            previous table's data. Page 16 carries two tables and the second
            one's band reaches the first one's final row
            (``Operating income $ 2,367 …``) without this floor.

    Note:
        Fragments are filtered by their own top edge rather than trusting the
        clip rectangle. ``get_text(clip=…)`` returns any line that *intersects*
        the rectangle, so a row sitting just above the band still comes back.
    """
    x0, y0, x1, _ = table_bbox
    top = max(0.0, y0 - band)
    if floor_y is not None:
        top = max(top, floor_y)
    clip = pymupdf.Rect(x0 - 5, top, x1 + 5, y0 - 1)

    fragments: list[Fragment] = []
    for block in page.get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", [])).strip()
            if not text:
                continue
            bx0, by0, bx1, _ = line["bbox"]
            if by0 < top:
                continue
            fragments.append(Fragment(text=text, x0=bx0, x1=bx1, y=by0))
    return fragments


def _drop_prose(fragments: list[Fragment], table_width: float) -> list[Fragment]:
    """Remove caption sentences, statement titles, and units lines.

    Header cells are narrow; the caption and the units parenthetical run most of
    the page width. Width alone separates them reliably in this document.
    """
    limit = table_width * _MAX_CAPTION_WIDTH_RATIO
    return [f for f in fragments if f.width <= limit]


def _cluster_rows(fragments: list[Fragment]) -> list[list[Fragment]]:
    """Group fragments into rows by vertical position, top to bottom."""
    rows: list[list[Fragment]] = []
    for fragment in sorted(fragments, key=lambda f: (f.y, f.x0)):
        if rows and abs(fragment.y - rows[-1][0].y) <= _Y_TOLERANCE:
            rows[-1].append(fragment)
        else:
            rows.append([fragment])
    for row in rows:
        row.sort(key=lambda f: f.x0)
    return rows


def _merge_into_columns(rows: list[list[Fragment]]) -> list[HeaderColumn]:
    """Merge vertically stacked fragments that share a horizontal span.

    ``June 25,`` above ``2022`` becomes ``June 25, 2022``. A fragment with no
    partner above or below, such as ``Change``, stands alone.
    """
    columns: list[list[Fragment]] = []

    for row in rows:
        for fragment in row:
            for column in columns:
                if any(fragment.overlaps(existing) for existing in column):
                    column.append(fragment)
                    break
            else:
                columns.append([fragment])

    merged: list[HeaderColumn] = []
    for column in columns:
        ordered = sorted(column, key=lambda f: f.y)
        label = " ".join(f.text for f in ordered)
        label = re.sub(r",\s+", ", ", label).strip()
        merged.append(
            HeaderColumn(
                label=label,
                x0=min(f.x0 for f in ordered),
                x1=max(f.x1 for f in ordered),
            )
        )

    return sorted(merged, key=lambda c: c.x0)


def _apply_groups(leaves: list[HeaderColumn], groups: list[Fragment]) -> list[HeaderColumn]:
    """Attach group labels to leaf headers using midpoint boundaries.

    A group header is centred over the columns it heads rather than spanning
    them, so x-overlap does not work: on page 18 ``Three Months Ended`` overlaps
    only one of its three columns. Splitting the axis at the midpoint between
    consecutive group headers assigns every leaf correctly.
    """
    if not groups:
        return leaves

    ordered = sorted(groups, key=lambda f: f.x0)
    boundaries = [
        (ordered[i].x1 + ordered[i + 1].x0) / 2 for i in range(len(ordered) - 1)
    ]

    labelled: list[HeaderColumn] = []
    for leaf in leaves:
        index = 0
        while index < len(boundaries) and leaf.centre > boundaries[index]:
            index += 1
        labelled.append(
            HeaderColumn(label=leaf.label, x0=leaf.x0, x1=leaf.x1, group=ordered[index].text)
        )
    return labelled


def recover_headers(
    page: pymupdf.Page,
    table_bbox: tuple[float, float, float, float],
    band: float = _DEFAULT_BAND,
    floor_y: float | None = None,
) -> list[HeaderColumn]:
    """Recover the column headers above a table.

    Args:
        page: The page the table sits on.
        table_bbox: The detected table's bounding box.
        band: How far above the table to search, in points.
        floor_y: Bottom edge of the preceding table on this page, if any. The
            search never crosses it.

    Returns:
        Leaf headers in left-to-right order, each carrying its group label where
        one applies. Empty if no header structure could be found, which the
        caller must treat as :attr:`TableValidation.HEADERS_UNRESOLVED` rather
        than as a table with no headers.
    """
    table_width = table_bbox[2] - table_bbox[0]
    fragments = _drop_prose(
        _collect_fragments(page, table_bbox, band, floor_y), table_width
    )
    if not fragments:
        return []

    rows = _cluster_rows(fragments)
    # A lone fragment on its own row is a stray label, not a header row.
    rows = [row for row in rows if len(row) >= 2]
    if not rows:
        return []

    # The topmost row is the group level when it carries fewer cells than the
    # widest row below it — two "Months Ended" spans over four date columns.
    widest = max(len(row) for row in rows)
    group_row: list[Fragment] = []
    if len(rows) > 1 and len(rows[0]) < widest:
        group_row = rows[0]
        rows = rows[1:]

    leaves = _merge_into_columns(rows)
    return _apply_groups(leaves, group_row)


def assign_to_columns(
    headers: list[HeaderColumn],
    column_spans: list[tuple[float, float]],
    table_x0: float | None = None,
) -> list[str]:
    """Map recovered headers onto the table's actual columns by x-overlap.

    Args:
        headers: Recovered leaf headers.
        column_spans: ``(x0, x1)`` for each column of the detected table.
        table_x0: Left edge of the table. Columns flush with it are row-label
            columns and are never given a period header — see below.

    Returns:
        One label per column; empty string where no header covers that column,
        which is normal for the leading row-label column.

    Note:
        Several columns legitimately share one label: the ``$`` column and the
        value column beside it both belong to the same period, and the collapse
        step later merges them.

        The row-label column is identified geometrically rather than by index.
        On a table whose cells were mis-parsed, the label column's span can
        stretch far enough right to overlap the first period header — page 16's
        second table does exactly this, being the one where PyMuPDF merges
        ``$ 119,455 $ 106,`` into a single cell. Anchoring on the left edge
        keeps the label column unlabelled even when the geometry is corrupt.
    """
    labels: list[str] = []
    for span_x0, span_x1 in column_spans:
        if table_x0 is not None and span_x0 <= table_x0 + _LABEL_COLUMN_TOLERANCE:
            labels.append("")
            continue

        best: HeaderColumn | None = None
        best_overlap = 0.0
        for header in headers:
            overlap = min(span_x1, header.x1) - max(span_x0, header.x0)
            if overlap > best_overlap:
                best, best_overlap = header, overlap
        labels.append(best.full_label if best is not None else "")
    return labels
