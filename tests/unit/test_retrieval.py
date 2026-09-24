"""Query routing, fusion, and context assembly."""

from __future__ import annotations

from app.models import Chunk, ContentType, Provenance, TablePayload, TableValidation
from app.retrieval import ContextBuilder, reciprocal_rank_fusion, route


def chunk(chunk_id: str, content_type: ContentType = ContentType.TEXT, text: str = "body") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        content_type=content_type,
        embed_text=text,
        display_text=text,
        provenance=Provenance(document_id="D", pdf_page=4, printed_page=1, section="Ops"),
    )


class TestRouter:
    def test_figure_words_prefer_figures(self):
        assert route("What does the chart on page 3 show?")[ContentType.FIGURE] == 1.0

    def test_financial_words_prefer_tables(self):
        assert route("What were total net sales in the quarter?")[ContentType.TABLE] == 1.0

    def test_a_bare_number_prefers_tables(self):
        assert route("Where does 82,959 appear?")[ContentType.TABLE] == 1.0

    def test_narrative_words_prefer_text(self):
        assert route("What does the filing say about risk?")[ContentType.TEXT] == 1.0

    def test_unrecognised_query_expresses_no_preference(self):
        assert not any(route("Tell me about Cupertino").values())

    def test_routing_never_excludes_a_type(self):
        """A bias of zero means 'no boost', never 'filter out'."""
        bias = route("What does the chart show?")
        assert set(bias) == set(ContentType)
        assert all(weight >= 0 for weight in bias.values())


class TestFusion:
    def test_a_chunk_found_by_both_outranks_one_found_by_either(self):
        shared, dense_only, lexical_only = chunk("shared"), chunk("dense"), chunk("lexical")
        fused = reciprocal_rank_fusion(
            dense=[(dense_only, 0.9), (shared, 0.8)],
            lexical=[(lexical_only, 5.0), (shared, 4.0)],
        )
        assert fused[0].chunk.chunk_id == "shared"
        assert fused[0].found_by == "both"

    def test_ranks_are_assigned_in_order(self):
        fused = reciprocal_rank_fusion([(chunk("a"), 1.0), (chunk("b"), 0.5)], [])
        assert [entry.rank for entry in fused] == [1, 2]

    def test_provenance_of_discovery_is_recorded(self):
        fused = reciprocal_rank_fusion([(chunk("a"), 1.0)], [(chunk("b"), 1.0)])
        by_id = {entry.chunk.chunk_id: entry for entry in fused}
        assert by_id["a"].found_by == "dense"
        assert by_id["b"].found_by == "lexical"

    def test_empty_inputs_produce_no_results(self):
        assert reciprocal_rank_fusion([], []) == []

    def test_boost_promotes_a_near_neighbour(self):
        """A preferred type just below the top should be able to overtake it."""
        dense = [(chunk("text-1", ContentType.TEXT), 0.99), (chunk("table-2", ContentType.TABLE), 0.98)]
        fused = reciprocal_rank_fusion(dense, [], bias={ContentType.TABLE: 1.0}, boost_weight=0.05)
        assert fused[0].chunk.chunk_id == "table-2"

    def test_boost_cannot_promote_a_distant_result(self):
        """The bound is r < 1 + (k+1)*boost. At k=60 and boost=0.05 that is ~4."""
        dense = [(chunk("text-1", ContentType.TEXT), 0.99)]
        dense += [(chunk(f"filler-{i}", ContentType.TEXT), 0.5) for i in range(10)]
        dense.append((chunk("table-12", ContentType.TABLE), 0.1))

        fused = reciprocal_rank_fusion(dense, [], bias={ContentType.TABLE: 1.0}, boost_weight=0.05)
        assert fused[0].chunk.chunk_id == "text-1"

    def test_boost_of_zero_changes_nothing(self):
        dense = [(chunk("text-1", ContentType.TEXT), 0.99), (chunk("table-2", ContentType.TABLE), 0.98)]
        fused = reciprocal_rank_fusion(dense, [], bias={ContentType.TABLE: 1.0}, boost_weight=0.0)
        assert fused[0].chunk.chunk_id == "text-1"


class TestContextBuilder:
    def _entry(self, c: Chunk, rank: int = 1):
        from app.retrieval.fusion import RetrievedChunk

        return RetrievedChunk(chunk=c, score=1.0, rank=rank)

    def test_evidence_is_fenced_as_data_not_instructions(self):
        built = ContextBuilder().build([self._entry(chunk("a"))])
        assert "<<<EVIDENCE" in built.text and "EVIDENCE>>>" in built.text

    def test_every_block_carries_its_citation(self):
        built = ContextBuilder().build([self._entry(chunk("a"))])
        assert "Page 1 (PDF p. 4)" in built.text

    def test_duplicate_content_is_collapsed(self):
        """Segment figures appear in Note 9 and again in MD&A."""
        first = chunk("note-9", text="Americas net sales were 37,472")
        second = chunk("mdna", text="Americas net sales were 37,472")
        built = ContextBuilder().build([self._entry(first, 1), self._entry(second, 2)])
        assert len(built.used) == 1

    def test_unreliable_table_carries_a_warning(self):
        unreliable = Chunk(
            chunk_id="t",
            content_type=ContentType.TABLE,
            embed_text="x",
            display_text="x",
            provenance=Provenance(document_id="D", pdf_page=16, printed_page=13),
            payload=TablePayload(validation=TableValidation.COLUMNS_AMBIGUOUS),
        )
        built = ContextBuilder().build([self._entry(unreliable)])
        assert "WARNING" in built.text
        assert "columns_ambiguous" in built.text

    def test_budget_is_respected_and_overflow_reported(self):
        # Distinct bodies, or deduplication collapses them before the budget applies.
        entries = [
            self._entry(chunk(f"c{i}", text=f"block {i} " + "x" * 500), i) for i in range(20)
        ]
        built = ContextBuilder(budget_chars=1200).build(entries)
        assert built.dropped > 0
        assert len(built.used) < 20

    def test_no_results_yields_an_empty_context(self):
        built = ContextBuilder().build([])
        assert built.is_empty
        assert built.text == ""
