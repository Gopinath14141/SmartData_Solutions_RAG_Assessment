"""Domain model invariants."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import (
    Chunk,
    ContentType,
    FigurePayload,
    Provenance,
    TablePayload,
    TableValidation,
)


class TestProvenance:
    def test_printed_page_leads_the_citation(self, provenance: Provenance):
        assert provenance.citation == "Page 15 (PDF p. 18)"

    def test_falls_back_to_pdf_index_without_a_printed_page(self):
        bare = Provenance(document_id="D", pdf_page=1)
        assert bare.citation == "PDF p. 1"

    def test_location_prefers_the_most_specific_label(self):
        with_note = Provenance(
            document_id="D", pdf_page=16, section="Segments", note="Note 9 – Segments"
        )
        assert with_note.location == "Note 9 – Segments"

    def test_location_has_a_defined_fallback(self):
        assert Provenance(document_id="D", pdf_page=1).location == "Unattributed"

    def test_page_numbers_must_be_positive(self):
        with pytest.raises(ValidationError):
            Provenance(document_id="D", pdf_page=0)


class TestTablePayload:
    def test_only_ok_tables_are_trustworthy(self):
        assert TablePayload().is_trustworthy
        for failure in (
            TableValidation.HEADERS_UNRESOLVED,
            TableValidation.COLUMNS_AMBIGUOUS,
            TableValidation.WIDTH_MISMATCH,
            TableValidation.PARSE_FAILED,
        ):
            assert not TablePayload(validation=failure).is_trustworthy


class TestPayloadDiscriminator:
    """Both guards are needed to stop a figure deserialising as an empty table.

    Every TablePayload field has a default, so without the ``kind`` tag a
    figure payload validates as a table with no rows, and without
    ``extra="forbid"`` a payload merely mislabelled does the same. Either would
    corrupt silently through the Qdrant round-trip rather than failing loudly.
    """

    def _figure_chunk(self, provenance: Provenance) -> Chunk:
        return Chunk(
            chunk_id="D::p001::figure::0",
            content_type=ContentType.FIGURE,
            embed_text="logo",
            display_text="logo",
            provenance=provenance,
            payload=FigurePayload(image_path=Path("a.jpeg"), width=46, height=56),
        )

    def test_payload_type_survives_a_round_trip(self, provenance: Provenance):
        original = self._figure_chunk(provenance)
        revived = Chunk.model_validate(original.model_dump(mode="json"))
        assert isinstance(revived.payload, FigurePayload)
        assert revived.figure is not None
        assert revived.table is None

    def test_untagged_payload_is_rejected(self, provenance: Provenance):
        dumped = self._figure_chunk(provenance).model_dump(mode="json")
        dumped["payload"] = {k: v for k, v in dumped["payload"].items() if k != "kind"}
        with pytest.raises(ValidationError):
            Chunk.model_validate(dumped)

    def test_mislabelled_payload_is_rejected(self, provenance: Provenance):
        dumped = self._figure_chunk(provenance).model_dump(mode="json")
        dumped["payload"] = {**dumped["payload"], "kind": "table"}
        with pytest.raises(ValidationError):
            Chunk.model_validate(dumped)


class TestChunkAccessors:
    def test_text_chunk_has_neither_payload(self, text_chunk: Chunk):
        assert text_chunk.table is None
        assert text_chunk.figure is None
