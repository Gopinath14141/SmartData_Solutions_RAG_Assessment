"""Grounded answer generation over retrieved evidence."""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.errors import GenerationError, LLMUnavailableError
from app.generation.prompts import ANSWER_SYSTEM_PROMPT, VISION_SYSTEM_PROMPT, build_user_prompt
from app.models import ContentType
from app.retrieval import BuiltContext, ContextBuilder, HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)

_MAX_VISION_PAGES = 2
"""Page images per vision call. Each is a full-page render and costs real tokens."""


@dataclass
class Evidence:
    """One piece of evidence, as presented to the user."""

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
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[dict[str, str]] = field(default_factory=list)
    table_trustworthy: bool = True
    figure_image: str | None = None
    figure_decorative: bool = False

    @classmethod
    def from_retrieved(cls, entry: RetrievedChunk, cited: bool) -> Evidence:
        chunk = entry.chunk
        table = chunk.table
        figure = chunk.figure
        return cls(
            chunk_id=chunk.chunk_id,
            content_type=chunk.content_type.value,
            citation=chunk.provenance.citation,
            printed_page=chunk.provenance.printed_page,
            pdf_page=chunk.provenance.pdf_page,
            section=chunk.provenance.location,
            rank=entry.rank,
            found_by=entry.found_by,
            cited_by_model=cited,
            display_text=chunk.display_text,
            table_markdown=table.markdown if table else None,
            table_headers=table.column_headers if table else [],
            table_rows=table.rows if table else [],
            table_trustworthy=table.is_trustworthy if table else True,
            figure_image=str(figure.image_path) if figure else None,
            figure_decorative=figure.is_decorative if figure else False,
        )


@dataclass
class Answer:
    """A generated answer with its evidence and timings."""

    question: str
    text: str
    refused: bool
    evidence: list[Evidence]
    model: str
    used_vision: bool
    routed_to: str
    seconds_retrieve: float
    seconds_generate: float
    chunks_retrieved: int
    context_characters: int
    content_mix: dict[str, int]

    @property
    def seconds_total(self) -> float:
        return self.seconds_retrieve + self.seconds_generate

    @property
    def cited(self) -> list[Evidence]:
        return [item for item in self.evidence if item.cited_by_model]


class Answerer:
    """Retrieves evidence and generates a grounded answer."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        context_builder: ContextBuilder | None = None,
        settings: Settings | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.retriever = retriever or HybridRetriever(settings=self.settings)
        self.context_builder = context_builder or ContextBuilder(self.settings)
        self._client = client or OpenAI(
            api_key=self.settings.require_api_key(),
            timeout=self.settings.llm_timeout_seconds,
        )

    # ── model plumbing ────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _complete(self, model: str, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=self.settings.llm_temperature,
            max_tokens=self.settings.llm_max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _parse(raw: str) -> tuple[str, bool, list[str]]:
        """Parse the model's JSON reply, tolerating a malformed one.

        A parse failure must not be reported as a confident answer, so the
        fallback refuses rather than passing the raw text through.
        """
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("model returned non-JSON output")
            return (
                "The answer could not be read back from the model. Please retry.",
                True,
                [],
            )

        text = str(payload.get("answer", "")).strip()
        refused = bool(payload.get("refused", False))
        cited = [str(item) for item in payload.get("cited_chunk_ids", []) or []]

        if not text:
            return ("No answer was produced.", True, cited)
        return text, refused, cited

    @staticmethod
    def _encode_image(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    # ── vision routing ────────────────────────────────────────────────────

    def _vision_pages(self, context: BuiltContext) -> list[Path]:
        """Page renders to attach, if this question needs to be read visually.

        Two triggers, both from architecture §8: a genuine figure question, and
        a retrieved table whose parsed structure failed validation — where the
        rendered page is better evidence than the mis-parsed grid.
        """
        pages: list[Path] = []
        for entry in context.used:
            chunk = entry.chunk
            figure = chunk.figure
            table = chunk.table

            wants_vision = (figure is not None and not figure.is_decorative) or (
                table is not None and not table.is_trustworthy
            )
            if not wants_vision:
                continue

            if figure is not None and figure.page_render_path:
                candidate = figure.page_render_path
            else:
                candidate = (
                    self.settings.renders_dir / f"page_{chunk.provenance.pdf_page:03d}.png"
                )

            if candidate.exists() and candidate not in pages:
                pages.append(candidate)
            if len(pages) >= _MAX_VISION_PAGES:
                break

        return pages

    # ── public API ────────────────────────────────────────────────────────

    def answer(
        self,
        question: str,
        top_k: int | None = None,
        content_types: list[ContentType] | None = None,
    ) -> Answer:
        """Answer a question from the filing.

        Raises:
            EmptyQueryError: Blank question.
            LLMUnavailableError: The model could not be reached.
        """
        retrieval = self.retriever.retrieve(question, top_k=top_k, content_types=content_types)
        context = self.context_builder.build(retrieval.results)

        vision_pages = self._vision_pages(context)
        use_vision = bool(vision_pages)
        model = self.settings.vision_model if use_vision else self.settings.llm_model
        system = VISION_SYSTEM_PROMPT if use_vision else ANSWER_SYSTEM_PROMPT

        content: list[dict] = [{"type": "text", "text": build_user_prompt(question, context.text)}]
        for page in vision_pages:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{self._encode_image(page)}"},
                }
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content if use_vision else content[0]["text"]},
        ]

        started = time.perf_counter()
        try:
            raw = self._complete(model, messages)
        except (RateLimitError, APIConnectionError) as exc:
            raise LLMUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc
        except APIStatusError as exc:
            raise GenerationError(
                "The model rejected the request.",
                detail=f"status={exc.status_code}: {exc}",
            ) from exc
        seconds_generate = time.perf_counter() - started

        text, refused, cited_ids = self._parse(raw)
        cited = set(cited_ids)

        # No evidence retrieved means refusal regardless of what the model said.
        if context.is_empty:
            refused = True

        return Answer(
            question=question,
            text=text,
            refused=refused,
            evidence=[Evidence.from_retrieved(e, e.chunk.chunk_id in cited) for e in context.used],
            model=model,
            used_vision=use_vision,
            routed_to=retrieval.routed_to,
            seconds_retrieve=retrieval.seconds_embed + retrieval.seconds_search,
            seconds_generate=seconds_generate,
            chunks_retrieved=len(retrieval.results),
            context_characters=context.characters,
            content_mix=retrieval.content_mix,
        )
