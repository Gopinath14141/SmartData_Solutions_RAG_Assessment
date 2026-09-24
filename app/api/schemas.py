"""Typed request and response models for the HTTP API.

The contract is in docs/architecture.md §12. ``refused`` is an explicit field
rather than something the client infers from the answer text: a refusal is a
distinct outcome and must be representable as one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.generation import Answer, Evidence
from app.models import ContentType


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    content_types: list[ContentType] | None = Field(
        default=None,
        description="Hard restriction on content type. The router's preference is separate and soft.",
    )
    top_k: int | None = Field(default=None, ge=1, le=50)


class EvidenceResponse(BaseModel):
    chunk_id: str
    content_type: str
    citation: str
    printed_page: int | None
    pdf_page: int
    section: str
    rank: int
    found_by: str
    cited_by_model: bool
    display_text: str

    table_markdown: str | None = None
    table_headers: list[str] = Field(default_factory=list)
    table_rows: list[dict[str, str]] = Field(default_factory=list)
    table_trustworthy: bool = True

    figure_url: str | None = None
    figure_decorative: bool = False
    page_render_url: str

    @classmethod
    def from_evidence(cls, item: Evidence) -> EvidenceResponse:
        return cls(
            chunk_id=item.chunk_id,
            content_type=item.content_type,
            citation=item.citation,
            printed_page=item.printed_page,
            pdf_page=item.pdf_page,
            section=item.section,
            rank=item.rank,
            found_by=item.found_by,
            cited_by_model=item.cited_by_model,
            display_text=item.display_text,
            table_markdown=item.table_markdown,
            table_headers=item.table_headers,
            table_rows=item.table_rows,
            table_trustworthy=item.table_trustworthy,
            figure_url=f"/api/figures/{item.chunk_id}" if item.figure_image else None,
            figure_decorative=item.figure_decorative,
            page_render_url=f"/api/pages/{item.pdf_page}/render",
        )


class Telemetry(BaseModel):
    """What plan §29 asks to be recorded, surfaced in the interface."""

    model: str
    used_vision: bool
    routed_to: str
    chunks_retrieved: int
    content_mix: dict[str, int]
    context_characters: int
    seconds_retrieve: float
    seconds_generate: float
    seconds_total: float


class QueryResponse(BaseModel):
    question: str
    answer: str
    refused: bool
    evidence: list[EvidenceResponse]
    telemetry: Telemetry

    @classmethod
    def from_answer(cls, answer: Answer) -> QueryResponse:
        return cls(
            question=answer.question,
            answer=answer.text,
            refused=answer.refused,
            evidence=[EvidenceResponse.from_evidence(item) for item in answer.evidence],
            telemetry=Telemetry(
                model=answer.model,
                used_vision=answer.used_vision,
                routed_to=answer.routed_to,
                chunks_retrieved=answer.chunks_retrieved,
                content_mix=answer.content_mix,
                context_characters=answer.context_characters,
                seconds_retrieve=round(answer.seconds_retrieve, 3),
                seconds_generate=round(answer.seconds_generate, 3),
                seconds_total=round(answer.seconds_total, 3),
            ),
        )


class HealthResponse(BaseModel):
    ok: bool
    document_present: bool
    api_key_configured: bool
    llm_model: str
    vision_model: str
    embedding_model: str
    qdrant: dict[str, object]


class ExampleQuestion(BaseModel):
    question: str
    category: str
    note: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
