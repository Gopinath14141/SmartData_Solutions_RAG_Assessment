"""Embedding generation via the OpenAI API.

The only place in the pipeline that knows which embedding provider is in use.
Extraction, chunking, and retrieval depend on the :class:`Embedder` interface
rather than on OpenAI, so swapping providers touches this file alone
(docs/architecture.md §11).
"""

from __future__ import annotations

import logging

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings, get_settings
from app.errors import GenerationError, LLMUnavailableError

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64
"""Texts per API call. The whole corpus is ~160 chunks, so this is 3 calls."""


class Embedder:
    """Turns text into vectors."""

    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client or OpenAI(
            api_key=self.settings.require_api_key(),
            timeout=self.settings.llm_timeout_seconds,
        )

    @property
    def model(self) -> str:
        return self.settings.embedding_model

    @property
    def dimensions(self) -> int:
        return self.settings.embedding_dimensions

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
        )
        # The API guarantees order, but index explicitly rather than trusting it:
        # a silent misalignment here would attach every vector to the wrong chunk.
        return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, batched.

        Raises:
            LLMUnavailableError: The API could not be reached.
            GenerationError: The API rejected the request.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), _BATCH_SIZE):
                batch = texts[start : start + _BATCH_SIZE]
                vectors.extend(self._embed_batch(batch))
                logger.debug("embedded %d/%d", len(vectors), len(texts))
        except (RateLimitError, APIConnectionError) as exc:
            raise LLMUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc
        except APIStatusError as exc:
            raise GenerationError(
                "The embedding request was rejected.",
                detail=f"status={exc.status_code}: {exc}",
            ) from exc

        if len(vectors) != len(texts):
            raise GenerationError(
                "The embedding service returned an unexpected number of vectors.",
                detail=f"expected {len(texts)}, got {len(vectors)}",
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        return self.embed_texts([text])[0]
