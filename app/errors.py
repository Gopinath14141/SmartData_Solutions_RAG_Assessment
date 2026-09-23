"""Typed exception hierarchy for the RAG pipeline.

Plan §17 requires that end users never see a raw stack trace. Every failure mode
that a user can plausibly cause is represented here, and each exception carries a
``user_message`` written for a human alongside optional technical ``detail`` for
the logs. The API layer renders ``user_message``; the logger records ``detail``.
"""

from __future__ import annotations


class RAGError(Exception):
    """Base class for every error this application raises deliberately.

    Args:
        message: User-facing text. Falls back to the subclass default.
        detail: Technical context for logs. Never shown to the user.
    """

    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.user_message = message or self.default_message
        self.detail = detail
        super().__init__(self.user_message if detail is None else f"{self.user_message} | {detail}")


# ─────────────────────────── Configuration ───────────────────────────


class ConfigurationError(RAGError):
    """Invalid or missing configuration."""

    default_message = "The application is not configured correctly."


class MissingAPIKeyError(ConfigurationError):
    """No OpenAI API key is available.

    Detected at startup rather than at first query, so the failure surfaces
    before a user has typed anything (plan §17).
    """

    default_message = (
        "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
    )


# ───────────────────────────── Ingestion ─────────────────────────────


class IngestionError(RAGError):
    """The source document could not be ingested."""

    default_message = "The document could not be processed."


class DocumentNotFoundError(IngestionError):
    default_message = (
        "The source PDF was not found. Run `python scripts/download_document.py` first."
    )


class InvalidPDFError(IngestionError):
    default_message = "The file is not a readable PDF."


class EncryptedPDFError(IngestionError):
    """The PDF requires a password to open.

    Note that this is *not* raised for a PDF that merely carries an encryption
    dictionary with no user password — the source document for this project is
    exactly that case (docs/pdf-analysis.md §1), and it opens normally.
    """

    default_message = "The PDF is password-protected and cannot be opened."


# ───────────────────────────── Extraction ────────────────────────────


class ExtractionError(RAGError):
    default_message = "Content could not be extracted from the document."


class TableExtractionError(ExtractionError):
    default_message = "A table could not be extracted."


class FigureExtractionError(ExtractionError):
    default_message = "A figure could not be extracted."


class PageRenderError(ExtractionError):
    default_message = "A page could not be rendered to an image."


# ────────────────────────────── Indexing ─────────────────────────────


class IndexingError(RAGError):
    default_message = "The search index could not be built."


class VectorStoreError(RAGError):
    default_message = "The vector store is not available."


class VectorStoreUnavailableError(VectorStoreError):
    default_message = (
        "Cannot reach Qdrant. Start it with `docker compose up -d qdrant` and try again."
    )


class CollectionMissingError(VectorStoreError):
    default_message = (
        "The document has not been indexed yet. Run `python -m app.cli ingest` first."
    )


class EmbeddingModelMismatchError(VectorStoreError):
    """The collection was built with a different embedding model than is configured.

    Querying across a model change silently returns nonsense, so it is an error
    rather than a warning.
    """

    default_message = (
        "The existing index was built with a different embedding model. "
        "Re-run `python -m app.cli ingest --rebuild`."
    )


# ───────────────────────────── Retrieval ─────────────────────────────


class RetrievalError(RAGError):
    default_message = "The query could not be answered."


class EmptyQueryError(RetrievalError):
    """Rejected before any API call is made."""

    default_message = "Please enter a question."


# ───────────────────────────── Generation ────────────────────────────


class GenerationError(RAGError):
    default_message = "The answer could not be generated."


class LLMUnavailableError(GenerationError):
    default_message = (
        "The language model is unavailable right now. Please try again in a moment."
    )
