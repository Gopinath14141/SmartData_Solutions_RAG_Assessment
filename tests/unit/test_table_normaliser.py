"""Column collapse, value normalisation, and validation.

These use hand-built rows that reproduce the EDGAR layout exactly, including
the stagger: a value sits in a different raw column depending on whether a
currency symbol precedes it.
"""

from __future__ import annotations

from app.extraction.table_normaliser import (
    ColumnKind,
    classify_column,
    collapse,
    normalise_value,
    to_markdown,
    validate,
)
from app.models import TableValidation

THREE_2022 = "Three Months Ended | June 25, 2022"
THREE_2021 = "Three Months Ended | June 26, 2021"
CHANGE = "Three Months Ended | Change"


class TestClassifyColumn:
    def test_empty_column(self):
        assert classify_column(["", "  ", ""]) is ColumnKind.EMPTY

    def test_currency_only_column(self):
        assert classify_column(["$", "", "$"]) is ColumnKind.CURRENCY

    def test_percent_only_column(self):
        assert classify_column(["%", "%"]) is ColumnKind.PERCENT

    def test_mixed_symbol_and_value_is_data(self):
        """The stagger: this column holds "$" on one row and a value on another."""
        assert classify_column(["$", "7,382", ""]) is ColumnKind.DATA


class TestNormaliseValue:
    def test_parenthesised_negative_becomes_a_minus(self):
        assert normalise_value("(10)") == "-10"
        assert normalise_value("(1,234.5)") == "-1,234.5"

    def test_symbols_are_attached(self):
        assert normalise_value("63,355", currency="$") == "$63,355"
        assert normalise_value("3", percent="%") == "3%"

    def test_negative_percentage_keeps_its_sign(self):
        assert normalise_value("(10)", percent="%") == "-10%"

    def test_blank_stays_blank(self):
        assert normalise_value("   ") == ""


class TestCollapse:
    def test_statements_layout_twelve_columns_become_five(self):
        labels = ["", THREE_2022, THREE_2022, "", THREE_2021, THREE_2021]
        rows = [
            ["Products", "$", "63,355", "", "$", "63,948"],
            ["Services", "19,604", "", "", "17,486", ""],
        ]
        headers, collapsed = collapse(labels, rows)

        assert headers == ["", THREE_2022, THREE_2021]
        assert collapsed[0] == ["Products", "$63,355", "$63,948"]

    def test_staggered_row_without_currency_does_not_gain_one(self):
        labels = ["", THREE_2022, THREE_2022]
        rows = [["Products", "$", "63,355"], ["Services", "19,604", ""]]
        _, collapsed = collapse(labels, rows)

        assert collapsed[0][1] == "$63,355"
        assert collapsed[1][1] == "19,604"

    def test_percent_column_attaches_to_the_value_on_its_left(self):
        labels = ["", CHANGE, ""]
        rows = [["Mac", "(10)", "%"], ["iPhone", "3", "%"]]
        headers, collapsed = collapse(labels, rows)

        assert headers == ["", CHANGE]
        assert collapsed[0] == ["Mac", "-10%"]
        assert collapsed[1] == ["iPhone", "3%"]

    def test_fully_empty_rows_are_dropped(self):
        labels = ["", THREE_2022]
        rows = [["Products", "63,355"], ["", ""], ["Services", "19,604"]]
        _, collapsed = collapse(labels, rows)
        assert len(collapsed) == 2

    def test_empty_input_is_safe(self):
        assert collapse([], []) == ([], [])


class TestValidate:
    def test_clean_table_is_ok(self):
        assert validate(["", THREE_2022, THREE_2021], [["Products", "1", "2"]]) is TableValidation.OK

    def test_no_headers_is_unresolved(self):
        assert (
            validate(["", "", ""], [["Products", "1", "2"]])
            is TableValidation.HEADERS_UNRESOLVED
        )

    def test_duplicate_headers_are_ambiguous(self):
        """Two columns claiming one period means a lookup cannot be attributed."""
        assert (
            validate(["", THREE_2022, THREE_2022], [["Products", "1", "2"]])
            is TableValidation.COLUMNS_AMBIGUOUS
        )

    def test_two_numbers_in_one_cell_is_a_width_mismatch(self):
        assert (
            validate(["", THREE_2022], [["Segment operating income", "$ 119,455 $ 106,048"]])
            is TableValidation.WIDTH_MISMATCH
        )

    def test_row_labels_may_contain_numbers(self):
        """Only value cells are checked; a label like 'Note 5' must not trip it."""
        assert validate(["", THREE_2022], [["Note 5 – Debt 2022", "1,000"]]) is TableValidation.OK


class TestMarkdown:
    def test_renders_a_header_and_separator(self):
        rendered = to_markdown(["", THREE_2022], [["Products", "$63,355"]])
        lines = rendered.splitlines()
        assert lines[1].startswith("|---")
        assert "$63,355" in lines[2]

    def test_pipes_inside_headers_are_escaped(self):
        rendered = to_markdown(["", THREE_2022], [["Products", "1"]])
        assert r"\|" in rendered

    def test_empty_table_renders_nothing(self):
        assert to_markdown([], []) == ""
