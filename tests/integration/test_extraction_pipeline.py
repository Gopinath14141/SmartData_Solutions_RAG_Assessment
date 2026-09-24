"""Extraction against the real filing.

These assertions encode facts measured from the document in Phase 2 and
verified against the rendered pages. They are the regression net for the
extraction work: if header recovery or column collapse breaks, the values move.

No API key or vector store is required — nothing here calls a network service.
"""

from __future__ import annotations

import pytest

from app.chunking import build_chunks
from app.extraction import ExtractedFigure, ExtractedTable, TextBlock
from app.ingestion import HeadingKind, LoadedDocument
from app.models import ContentType, TableValidation

THREE_2022 = "Three Months Ended | June 25, 2022"
THREE_2021 = "Three Months Ended | June 26, 2021"
NINE_2022 = "Nine Months Ended | June 25, 2022"
NINE_2021 = "Nine Months Ended | June 26, 2021"


def row(table: ExtractedTable, prefix: str) -> dict[str, str] | None:
    for record in table.payload.rows:
        if record.get("label", "").strip().startswith(prefix):
            return record
    return None


def table_on(tables: list[ExtractedTable], page: int, index: int = 0) -> ExtractedTable:
    matches = [t for t in tables if t.provenance.pdf_page == page]
    return matches[index]


class TestDocumentStructure:
    def test_page_count(self, document: LoadedDocument):
        assert document.page_count == 28

    def test_printed_pages_are_offset_by_three(self, document: LoadedDocument):
        assert document.page_offset() == 3
        assert document.page(4).printed_page == 1
        assert document.page(25).printed_page == 22

    def test_pages_without_footers_have_no_printed_number(self, document: LoadedDocument):
        for page in (1, 2, 3, 26, 27, 28):
            assert document.page(page).printed_page is None

    def test_all_nine_notes_recovered(self, document: LoadedDocument):
        notes = [h for h in document.sections.headings if h.kind is HeadingKind.NOTE]
        assert len(notes) == 9

    def test_both_parts_recovered(self, document: LoadedDocument):
        parts = [h for h in document.sections.headings if h.kind is HeadingKind.PART]
        assert len(parts) == 2

    def test_running_head_is_not_a_heading(self, document: LoadedDocument):
        assert not any(h.text.strip() == "Apple Inc." for h in document.sections.headings)

    def test_section_resolves_by_position_within_a_page(self, document: LoadedDocument):
        """Page 23 carries Items 1, 1A and 2; position decides which applies."""
        items = [
            h for h in document.sections.headings
            if h.pdf_page == 23 and h.kind is HeadingKind.ITEM
        ]
        assert len(items) >= 2
        top = document.sections.state_at(23, items[0].top)
        bottom = document.sections.state_at(23, items[-1].top)
        assert top.item != bottom.item


class TestTableExtraction:
    def test_thirty_one_tables_found(self, tables: list[ExtractedTable]):
        assert len(tables) == 31

    def test_most_tables_validate(self, tables: list[ExtractedTable]):
        ok = sum(1 for t in tables if t.payload.validation is TableValidation.OK)
        assert ok >= 25, f"only {ok}/{len(tables)} validated"

    def test_no_table_is_silently_wrong(self, tables: list[ExtractedTable]):
        """Every table is either OK or explicitly flagged for visual fallback."""
        for table in tables:
            assert (table.payload.validation is TableValidation.OK) != table.needs_visual_fallback

    def test_column_inflation_is_substantially_removed(self, tables: list[ExtractedTable]):
        raw = sum(t.payload.raw_column_count for t in tables)
        collapsed = sum(len(t.payload.column_headers) for t in tables)
        assert collapsed < raw * 0.6, f"{raw} -> {collapsed}"


class TestStatementsOfOperations:
    """PDF page 4, printed page 1 — the table PyMuPDF's own header detection gets wrong."""

    @pytest.fixture
    def statements(self, tables: list[ExtractedTable]) -> ExtractedTable:
        return table_on(tables, 4)

    def test_twelve_raw_columns_collapse_to_five(self, statements: ExtractedTable):
        assert statements.payload.raw_column_count == 12
        assert len(statements.payload.column_headers) == 5

    def test_both_period_groups_are_recovered(self, statements: ExtractedTable):
        headers = statements.payload.column_headers
        assert THREE_2022 in headers
        assert NINE_2021 in headers

    def test_units_are_attached_from_outside_the_table(self, statements: ExtractedTable):
        assert "millions" in (statements.payload.units or "").lower()

    def test_total_net_sales_lands_in_the_right_periods(self, statements: ExtractedTable):
        total = row(statements, "Total net sales")
        assert total[THREE_2022] == "82,959"
        assert total[THREE_2021] == "81,434"
        assert total[NINE_2022] == "304,182"
        assert total[NINE_2021] == "282,457"

    def test_net_income(self, statements: ExtractedTable):
        assert row(statements, "Net income")[THREE_2022] == "$19,442"

    def test_parenthesised_negative_keeps_its_sign(self, statements: ExtractedTable):
        other = row(statements, "Other income/(expense)")
        assert other[THREE_2022] == "-10"
        assert other[NINE_2022] == "-97"

    def test_citation_uses_the_printed_page(self, statements: ExtractedTable):
        assert statements.provenance.citation == "Page 1 (PDF p. 4)"


class TestNetSalesByCategory:
    """PDF page 18 — eighteen raw columns, and the staggered currency layout."""

    @pytest.fixture
    def category(self, tables: list[ExtractedTable]) -> ExtractedTable:
        return table_on(tables, 18)

    def test_eighteen_raw_columns_collapse_to_seven(self, category: ExtractedTable):
        assert category.payload.raw_column_count == 18
        assert len(category.payload.column_headers) == 7

    def test_caption_is_attached(self, category: ExtractedTable):
        assert "net sales by category" in (category.payload.caption or "").lower()

    def test_iphone_values_and_change(self, category: ExtractedTable):
        iphone = row(category, "iPhone")
        assert iphone[THREE_2022] == "$40,665"
        assert iphone["Three Months Ended | Change"] == "3%"

    def test_negative_change_keeps_its_sign(self, category: ExtractedTable):
        assert row(category, "Mac")["Three Months Ended | Change"] == "-10%"

    def test_a_row_without_a_currency_symbol_does_not_gain_one(self, category: ExtractedTable):
        """The stagger: Mac's value sits in the column that holds "$" for iPhone."""
        assert row(category, "Mac")[THREE_2022] == "7,382"


class TestSegmentTable:
    """PDF page 16 — two tables, the second with corrupt cell geometry."""

    def test_segment_values(self, tables: list[ExtractedTable]):
        segment = table_on(tables, 16, 0)
        assert row(segment, "Net sales")[THREE_2022] == "$37,472"

    def test_headers_survive_the_preceding_table(self, tables: list[ExtractedTable]):
        """The second table's header band must not reach into the first table."""
        second = table_on(tables, 16, 1)
        assert any("Months Ended" in header for header in second.payload.column_headers)

    def test_corrupt_table_is_flagged_not_silently_accepted(self, tables: list[ExtractedTable]):
        second = table_on(tables, 16, 1)
        assert second.payload.validation is not TableValidation.OK
        assert second.needs_visual_fallback


class TestFigures:
    def test_exactly_one_image_in_the_document(self, figures: list[ExtractedFigure]):
        assert len(figures) == 1

    def test_it_is_the_cover_logo_and_is_marked_decorative(self, figures: list[ExtractedFigure]):
        logo = figures[0].payload
        assert figures[0].provenance.pdf_page == 1
        assert (logo.width, logo.height) == (46, 56)
        assert logo.is_decorative
        assert logo.caption is None

    def test_image_and_page_render_are_on_disk(self, figures: list[ExtractedFigure]):
        payload = figures[0].payload
        assert payload.image_path.exists()
        assert payload.page_render_path is not None and payload.page_render_path.exists()


class TestTextExtraction:
    def test_table_values_are_not_duplicated_as_narrative(
        self, text_blocks: list[TextBlock]
    ):
        page_four = " ".join(b.text for b in text_blocks if b.pdf_page == 4)
        assert "82,959" not in page_four

    def test_boilerplate_is_absent(self, text_blocks: list[TextBlock]):
        assert "Form 10-Q |" not in " ".join(b.text for b in text_blocks)

    def test_narrative_pages_still_yield_text(self, text_blocks: list[TextBlock]):
        page_seventeen = " ".join(b.text for b in text_blocks if b.pdf_page == 17)
        assert len(page_seventeen) > 1500


class TestChunking:
    @pytest.fixture
    def chunks(self, document, text_blocks, tables, figures):
        return build_chunks(document, text_blocks, tables, figures)

    def test_all_three_content_types_present(self, chunks):
        present = {c.content_type for c in chunks}
        assert present == set(ContentType)

    def test_one_chunk_per_table(self, chunks):
        assert sum(1 for c in chunks if c.content_type is ContentType.TABLE) == 31

    def test_figure_chunks_are_the_image_plus_an_inventory(self, chunks):
        """One chunk per image, plus a document-level inventory.

        The inventory exists because "what figures are in this document?" is a
        question about the corpus, which no per-figure chunk can answer. Without
        it the question retrieved unrelated prose and the model reported the
        exhibit certifications as images.
        """
        figures = [c for c in chunks if c.content_type is ContentType.FIGURE]
        assert len(figures) == 2

        inventory = [c for c in figures if c.chunk_id.endswith("figure-inventory")]
        assert len(inventory) == 1
        assert "no charts, graphs, diagrams, or data figures" in inventory[0].display_text
        assert inventory[0].provenance.section == "Document summary"

    def test_chunk_ids_are_unique(self, chunks):
        assert len({c.chunk_id for c in chunks}) == len(chunks)

    def test_no_empty_chunks(self, chunks):
        assert all(c.embed_text.strip() and c.display_text.strip() for c in chunks)

    def test_table_chunks_embed_row_labels_and_periods(self, chunks):
        table = next(
            c for c in chunks
            if c.content_type is ContentType.TABLE and c.provenance.pdf_page == 18
        )
        assert "iPhone" in table.embed_text
        assert "June 25, 2022" in table.embed_text

    def test_flagged_table_warns_the_generator(self, chunks):
        flagged = [
            c for c in chunks
            if c.content_type is ContentType.TABLE and c.table and not c.table.is_trustworthy
        ]
        assert flagged
        assert all("Structure unreliable" in c.display_text for c in flagged)

    def test_figure_chunk_does_not_claim_to_be_a_data_graphic(self, chunks):
        image = next(
            c for c in chunks
            if c.content_type is ContentType.FIGURE and c.figure is not None
        )
        assert "Decorative image" in image.display_text
