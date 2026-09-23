"""Detect and strip repeated page furniture, and recover printed page numbers.

Two problems from docs/pdf-analysis.md §6 and §2 share one solution.

The source document repeats a running head (``Apple Inc.``), a footer
(``Apple Inc. | Q3 2022 Form 10-Q | 15``), and a statement reference
(``See accompanying Notes to Condensed Consolidated Financial Statements.``).
Left in place, those three strings land in a dozen chunks and give every one of
them a spurious similarity boost on any query mentioning Apple or the notes.

Separately, the printed page number differs from the PDF index — by three in
this document — so citing the wrong one puts every reference three pages out.

Both fall out of the same observation: a footer is a line that recurs on many
pages and varies only in its digits. Normalise digits away and it becomes a
constant that frequency analysis finds on its own; the digits it varied by are
the printed page number. Nothing here hardcodes this filing's wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")
_TRAILING_NUMBER = re.compile(r"(\d+)\s*$")
_LETTER = re.compile(r"[a-z]")


def normalise(line: str) -> str:
    """Reduce a line to the form used for repetition counting.

    Collapses whitespace, lowercases, and replaces digit runs with ``#`` so that
    ``Apple Inc. | Q3 2022 Form 10-Q | 15`` and ``… | 16`` compare equal.
    """
    collapsed = _WHITESPACE.sub(" ", line).strip().lower()
    return _DIGITS.sub("#", collapsed)


@dataclass(frozen=True)
class BoilerplateProfile:
    """Which normalised lines are page furniture, learned from the whole document."""

    repeated: frozenset[str] = field(default_factory=frozenset)
    footer_forms: frozenset[str] = field(default_factory=frozenset)
    """Repeated lines that also carry a trailing number — printed-page candidates."""

    def is_boilerplate(self, line: str) -> bool:
        return normalise(line) in self.repeated


class BoilerplateDetector:
    """Learns page furniture from line-frequency across the document.

    Args:
        scan_lines: How many lines from each end of a page to consider. Headers
            and footers live at the extremes; scanning the whole page would
            misclassify genuinely repeated body text such as a recurring table
            row label.
        min_pages: A line must appear on at least this many pages to count as
            furniture. Guards against a phrase that happens to open two
            consecutive pages.
        min_fraction: …and on at least this fraction of pages. Both thresholds
            apply, so the rule scales to short and long documents alike.
    """

    def __init__(
        self,
        scan_lines: int = 3,
        min_pages: int = 3,
        min_fraction: float = 0.15,
    ) -> None:
        self.scan_lines = scan_lines
        self.min_pages = min_pages
        self.min_fraction = min_fraction

    def profile(self, pages_lines: list[list[str]]) -> BoilerplateProfile:
        """Build a profile from every page's lines.

        Args:
            pages_lines: One list of non-empty, stripped lines per page, in
                document order.
        """
        if not pages_lines:
            return BoilerplateProfile()

        # Count pages-containing, not occurrences: a line repeated three times
        # on one page is not furniture.
        pages_containing: dict[str, int] = {}
        for lines in pages_lines:
            for candidate in self._edge_lines(lines):
                key = normalise(candidate)
                if not self._can_be_furniture(key):
                    continue
                pages_containing[key] = pages_containing.get(key, 0) + 1

        threshold = max(self.min_pages, int(len(pages_lines) * self.min_fraction))
        repeated = {key for key, count in pages_containing.items() if count >= threshold}
        footers = {key for key in repeated if _TRAILING_NUMBER.search(key.replace("#", "0"))}

        return BoilerplateProfile(repeated=frozenset(repeated), footer_forms=frozenset(footers))

    @staticmethod
    def _can_be_furniture(normalised: str) -> bool:
        """Whether a normalised line is eligible to be classified as page furniture.

        Requires at least one letter. Without this guard, frequency analysis
        learns forms like ``'#,#'``, ``'$'`` and ``'®'`` — which match bare
        currency values sitting at a page edge. Page 13 ends with the figure
        ``53,325`` directly above the footer, so stripping backwards from the
        page end would delete a real number from a financial statement. Silent
        data loss of exactly the values this system exists to retrieve.

        Every footer in a paginated document of this kind carries words, so the
        restriction costs nothing here. A document whose footer is a bare page
        number would need the numeric case handled separately, and that is
        recorded as a limitation rather than guessed at.
        """
        return len(normalised) >= 4 and _LETTER.search(normalised) is not None

    def _edge_lines(self, lines: list[str]) -> list[str]:
        """The candidate header/footer lines at both ends of a page."""
        if len(lines) <= self.scan_lines * 2:
            return list(lines)
        return lines[: self.scan_lines] + lines[-self.scan_lines :]

    def printed_page_number(self, lines: list[str], profile: BoilerplateProfile) -> int | None:
        """Recover the page number printed in the footer, if there is one.

        Only footer-shaped boilerplate is considered, so a body line ending in a
        number — common in financial tables — is never mistaken for a page
        number.

        Returns:
            The printed page number, or None for pages without a footer (the
            cover, contents, and exhibit pages carry none).
        """
        for line in reversed(self._edge_lines(lines)):
            if normalise(line) not in profile.footer_forms:
                continue
            match = _TRAILING_NUMBER.search(line.strip())
            if match:
                return int(match.group(1))
        return None

    def strip(self, lines: list[str], profile: BoilerplateProfile) -> list[str]:
        """Remove furniture lines, preserving the order of what remains.

        Only lines at the page edges are removed. A sentence that legitimately
        appears mid-page is kept even if an identical line is furniture
        elsewhere.
        """
        if not lines:
            return []

        edge_count = min(self.scan_lines, len(lines) // 2) or len(lines)
        head_stop = 0
        for index in range(min(edge_count, len(lines))):
            if profile.is_boilerplate(lines[index]):
                head_stop = index + 1
            else:
                break

        tail_start = len(lines)
        for index in range(len(lines) - 1, max(head_stop - 1, len(lines) - edge_count - 1), -1):
            if profile.is_boilerplate(lines[index]):
                tail_start = index
            else:
                break

        return lines[head_stop:tail_start]
