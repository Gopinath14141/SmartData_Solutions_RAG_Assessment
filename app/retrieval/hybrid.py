"""Hybrid dense + lexical retrieval."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.errors import EmptyQueryError
from app.indexing import BM25Store, Embedder, QdrantStore
from app.models import ContentType
from app.retrieval.fusion import RetrievedChunk, reciprocal_rank_fusion
from app.retrieval.router import route

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Fused results plus the telemetry the UI and the report need."""

    results: list[RetrievedChunk]
    dense_count: int
    lexical_count: int
    routed_to: str
    seconds_embed: float
    seconds_search: float

    @property
    def content_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for entry in self.results:
            key = entry.chunk.content_type.value
            mix[key] = mix.get(key, 0) + 1
        return mix


class HybridRetriever:
    """Embeds a query, searches both indexes, and fuses the results."""

    def __init__(
        self,
        store: QdrantStore | None = None,
        embedder: Embedder | None = None,
        bm25: BM25Store | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or QdrantStore(self.settings)
        self.embedder = embedder or Embedder(self.settings)
        self._bm25 = bm25

    @property
    def bm25(self) -> BM25Store:
        """The lexical index, built on first use from the stored chunks.

        Rebuilt in memory rather than persisted: it takes milliseconds at this
        corpus size and removes a second artefact to keep in sync with Qdrant.
        """
        if self._bm25 is None:
            self._bm25 = BM25Store(self.store.all_chunks())
        return self._bm25

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        content_types: list[ContentType] | None = None,
    ) -> RetrievalResult:
        """Retrieve the most relevant chunks for a query.

        Args:
            query: The user's question.
            top_k: How many results to return.
            content_types: Hard restriction, used by evaluation and by the UI's
                explicit filters. The router's preference is separate and soft.

        Raises:
            EmptyQueryError: The query is blank — rejected before any API call.
        """
        if not query or not query.strip():
            raise EmptyQueryError()

        top_k = top_k or self.settings.retrieval_top_k

        embed_started = time.perf_counter()
        vector = self.embedder.embed_query(query)
        seconds_embed = time.perf_counter() - embed_started

        search_started = time.perf_counter()
        dense = self.store.search(vector, self.settings.dense_top_k, content_types)
        lexical = self.bm25.search(query, self.settings.bm25_top_k, content_types)
        seconds_search = time.perf_counter() - search_started

        fused = reciprocal_rank_fusion(
            dense,
            lexical,
            k=self.settings.rrf_k,
            bias=route(query),
            boost_weight=self.settings.router_boost,
        )

        logger.debug(
            "retrieved %d dense + %d lexical -> %d fused", len(dense), len(lexical), len(fused)
        )

        return RetrievalResult(
            results=fused[:top_k],
            dense_count=len(dense),
            lexical_count=len(lexical),
            routed_to=", ".join(
                content_type.value
                for content_type, weight in route(query).items()
                if weight > 0
            )
            or "no preference",
            seconds_embed=seconds_embed,
            seconds_search=seconds_search,
        )
