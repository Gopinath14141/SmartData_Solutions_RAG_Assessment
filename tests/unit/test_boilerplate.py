"""Boilerplate detection and printed-page recovery."""

from __future__ import annotations

from app.ingestion.boilerplate import BoilerplateDetector, normalise


class TestNormalise:
    def test_digits_collapse_so_footers_compare_equal(self):
        assert normalise("Apple Inc. | Q3 2022 Form 10-Q | 15") == normalise(
            "Apple Inc. | Q3 2022 Form 10-Q | 16"
        )

    def test_whitespace_and_case_are_ignored(self):
        assert normalise("  Apple   INC.  ") == normalise("apple inc.")


# Bodies must differ by more than a digit. Normalisation replaces digit runs with
# '#', so "sentence 1" and "sentence 2" are the same form — correct behaviour for a
# footer, but it would make this fixture self-defeating.
BODIES = (
    "Revenue recognition policies are described below.",
    "Derivative instruments are measured at fair value.",
    "Accrued warranty costs changed during the period.",
    "Commercial paper matured within the quarter.",
    "Segment results are reported to the chief operating decision maker.",
    "Deferred tax assets remain subject to realisation.",
    "Share-based compensation is recognised over the vesting term.",
    "Contingencies are evaluated at each reporting date.",
    "Marketable securities are classified as available for sale.",
    "Term debt is carried at amortised cost.",
)


class TestDetection:
    def _pages(self, footer_start: int = 1, count: int = 10) -> list[list[str]]:
        return [
            [
                "Apple Inc.",
                BODIES[index % len(BODIES)],
                f"Apple Inc. | Q3 2022 Form 10-Q | {footer_start + index}",
            ]
            for index in range(count)
        ]

    def test_learns_running_head_and_footer(self):
        profile = BoilerplateDetector().profile(self._pages())
        assert profile.is_boilerplate("Apple Inc.")
        assert profile.is_boilerplate("Apple Inc. | Q3 2022 Form 10-Q | 7")

    def test_unique_body_text_is_not_boilerplate(self):
        profile = BoilerplateDetector().profile(self._pages())
        assert not profile.is_boilerplate(BODIES[3])

    def test_recovers_printed_page_from_footer(self):
        detector = BoilerplateDetector()
        pages = self._pages()
        profile = detector.profile(pages)
        assert detector.printed_page_number(pages[0], profile) == 1
        assert detector.printed_page_number(pages[5], profile) == 6

    def test_page_without_footer_yields_none(self):
        detector = BoilerplateDetector()
        pages = self._pages()
        profile = detector.profile(pages)
        assert detector.printed_page_number(["Cover page", "No footer here"], profile) is None


class TestNumericSafety:
    """A bare currency value at a page edge must never be treated as furniture.

    Page 13 of the filing ends with the figure 53,325 directly above the
    footer. An earlier version learned '#,#' as a repeated form and stripped
    backwards from the page end, deleting a real value from a financial
    statement.
    """

    def _pages_ending_in_values(self) -> list[list[str]]:
        values = ["53,325", "41,110", "53,325", "27,905", "53,325", "18,200", "53,325", "9,010"]
        return [
            ["Apple Inc.", "Narrative.", value, f"Apple Inc. | Q3 2022 Form 10-Q | {index + 1}"]
            for index, value in enumerate(values)
        ]

    def test_repeated_numeric_line_is_not_learned_as_furniture(self):
        profile = BoilerplateDetector().profile(self._pages_ending_in_values())
        assert not profile.is_boilerplate("53,325")
        assert all(any(c.isalpha() for c in form) for form in profile.repeated)

    def test_value_survives_stripping_while_footer_is_removed(self):
        detector = BoilerplateDetector()
        pages = self._pages_ending_in_values()
        profile = detector.profile(pages)

        kept = detector.strip(pages[0], profile)
        assert "53,325" in kept
        assert not any("Form 10-Q |" in line for line in kept)

    def test_bare_symbols_are_not_furniture(self):
        pages = [["$", "Narrative.", "®"] for _ in range(8)]
        profile = BoilerplateDetector().profile(pages)
        assert not profile.is_boilerplate("$")
        assert not profile.is_boilerplate("®")
