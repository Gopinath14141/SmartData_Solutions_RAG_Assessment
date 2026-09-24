"""Qdrant-backed dense vector store.

The full :class:`~app.models.Provenance` travels into the Qdrant payload, so
filtering by content type, section, or page happens inside the engine rather
than by post-filtering results in application code (docs/architecture.md §7).

Search runs in exact mode. At ~160 vectors the HNSW approximation buys nothing
and costs determinism, which matters when evaluation results are being compared
across runs.
"""

from __future__ import annotations

import logging
import time
import uuid

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config import Settings, get_settings
from app.errors import (
    CollectionMissingError,
    EmbeddingModelMismatchError,
    VectorStoreError,
    VectorStoreUnavailableError,
)
from app.models import Chunk, ContentType

logger = logging.getLogger(__name__)

_MODEL_MARKER_ID = "00000000-0000-0000-0000-000000000000"
"""A reserved point recording which embedding model built the collection.

Qdrant has no collection-level metadata, and querying a collection built by a
different embedding model returns confident nonsense rather than an error. This
marker makes that failure detectable.
"""


class QdrantStore:
    """Dense vector storage and search."""

    def __init__(self, settings: Settings | None = None, client: QdrantClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client or QdrantClient(
            host=self.settings.qdrant_host,
            port=self.settings.qdrant_port,
            timeout=int(self.settings.qdrant_timeout_seconds),
        )

    @property
    def collection(self) -> str:
        return self.settings.qdrant_collection

    # ── lifecycle ─────────────────────────────────────────────────────────

    def wait_until_ready(self) -> None:
        """Block until Qdrant answers, or raise.

        Under Docker Compose the application can start before Qdrant is
        listening, and a transient connection refusal must not kill the process
        (docs/architecture.md §10).
        """
        last: Exception | None = None
        for attempt in range(self.settings.qdrant_startup_retries + 1):
            try:
                self._client.get_collections()
                return
            except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
                last = exc
                if attempt < self.settings.qdrant_startup_retries:
                    delay = self.settings.qdrant_startup_backoff_seconds
                    logger.info("Qdrant not ready (attempt %d), retrying in %.1fs", attempt + 1, delay)
                    time.sleep(delay)

        raise VectorStoreUnavailableError(detail=f"{type(last).__name__}: {last}")

    def exists(self) -> bool:
        try:
            return self._client.collection_exists(self.collection)
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc

    def create(self, recreate: bool = False) -> None:
        """Create the collection, optionally dropping an existing one."""
        try:
            if recreate and self.exists():
                self._client.delete_collection(self.collection)

            if not self.exists():
                self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(
                        size=self.settings.embedding_dimensions,
                        distance=models.Distance.COSINE,
                    ),
                )
                # Indexed payload fields make metadata filters cheap.
                for field in ("content_type", "pdf_page", "printed_page"):
                    schema = (
                        models.PayloadSchemaType.KEYWORD
                        if field == "content_type"
                        else models.PayloadSchemaType.INTEGER
                    )
                    self._client.create_payload_index(self.collection, field, field_schema=schema)
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc

    def _write_model_marker(self) -> None:
        self._client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=_MODEL_MARKER_ID,
                    vector=[0.0] * self.settings.embedding_dimensions,
                    payload={"marker": "index_metadata", "embedding_model": self.settings.embedding_model},
                )
            ],
        )

    def verify_model(self) -> None:
        """Raise if the collection was built with a different embedding model."""
        if not self.exists():
            raise CollectionMissingError(detail=self.collection)

        try:
            records = self._client.retrieve(self.collection, ids=[_MODEL_MARKER_ID], with_payload=True)
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc

        if not records:
            raise CollectionMissingError(
                "The index is incomplete. Re-run `python -m app.cli ingest --rebuild`.",
                detail="model marker absent",
            )

        stored = (records[0].payload or {}).get("embedding_model")
        if stored != self.settings.embedding_model:
            raise EmbeddingModelMismatchError(detail=f"indexed with {stored!r}, configured {self.settings.embedding_model!r}")

    # ── writing ───────────────────────────────────────────────────────────

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        """Store chunks and their vectors."""
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                "Internal error while indexing.",
                detail=f"{len(chunks)} chunks but {len(vectors)} vectors",
            )

        points = [
            models.PointStruct(
                # Deterministic ids, so re-indexing updates in place rather than
                # accumulating duplicates of the same chunk.
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                vector=vector,
                payload={
                    **chunk.model_dump(mode="json"),
                    "content_type": chunk.content_type.value,
                    "pdf_page": chunk.provenance.pdf_page,
                    "printed_page": chunk.provenance.printed_page,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        try:
            self._client.upsert(collection_name=self.collection, points=points, wait=True)
            self._write_model_marker()
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc

        return len(points)

    # ── reading ───────────────────────────────────────────────────────────

    def search(
        self,
        vector: list[float],
        limit: int,
        content_types: list[ContentType] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Nearest neighbours, optionally restricted by content type."""
        query_filter = None
        if content_types:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="content_type",
                        match=models.MatchAny(any=[t.value for t in content_types]),
                    )
                ]
            )

        try:
            response = self._client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
                search_params=models.SearchParams(exact=True),
            )
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc

        results: list[tuple[Chunk, float]] = []
        for point in response.points:
            payload = point.payload or {}
            if payload.get("marker") == "index_metadata":
                continue
            results.append((Chunk.model_validate(payload), float(point.score)))
        return results

    def all_chunks(self) -> list[Chunk]:
        """Every indexed chunk, for building the lexical index."""
        chunks: list[Chunk] = []
        offset = None
        try:
            while True:
                records, offset = self._client.scroll(
                    collection_name=self.collection,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for record in records:
                    payload = record.payload or {}
                    if payload.get("marker") == "index_metadata":
                        continue
                    chunks.append(Chunk.model_validate(payload))
                if offset is None:
                    break
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc

        return chunks

    def count(self) -> int:
        """Indexed chunks, excluding the metadata marker."""
        try:
            total = self._client.count(self.collection, exact=True).count
        except (ResponseHandlingException, UnexpectedResponse, OSError) as exc:
            raise VectorStoreUnavailableError(detail=f"{type(exc).__name__}: {exc}") from exc
        return max(0, total - 1)

    def health(self) -> dict[str, object]:
        """Reachability and collection state, for the API health endpoint."""
        try:
            self._client.get_collections()
        except Exception as exc:
            return {"reachable": False, "error": f"{type(exc).__name__}", "detail": str(exc)[:200]}

        if not self.exists():
            return {"reachable": True, "collection": self.collection, "indexed": False}

        return {
            "reachable": True,
            "collection": self.collection,
            "indexed": True,
            "chunks": self.count(),
            "embedding_model": self.settings.embedding_model,
        }
