"""FastAPI application: JSON API plus the static frontend.

Errors are translated from the typed hierarchy in :mod:`app.errors` into HTTP
responses carrying the user-facing message only. Stack traces go to the log,
never to the client (plan §17).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.schemas import (
    ErrorResponse,
    ExampleQuestion,
    HealthResponse,
    QueryRequest,
    QueryResponse,
)
from app.config import Settings, get_settings
from app.errors import (
    CollectionMissingError,
    EmptyQueryError,
    LLMUnavailableError,
    RAGError,
    VectorStoreUnavailableError,
)
from app.generation import Answerer
from app.indexing import QdrantStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

EXAMPLES = [
    ExampleQuestion(
        question="What were Apple's total net sales for the three months ended June 25, 2022?",
        category="table",
        note="Exact lookup in the Statements of Operations.",
    ),
    ExampleQuestion(
        question="How did Mac net sales change year over year in the third quarter?",
        category="table",
        note="Requires reading the Change column, which is negative.",
    ),
    ExampleQuestion(
        question="Which geographic segment had the highest operating income in the quarter?",
        category="table",
        note="Comparison across multiple rows of the segment table.",
    ),
    ExampleQuestion(
        question="What does the filing say about the impact of COVID-19?",
        category="text",
        note="Narrative answer from Management's Discussion and Analysis.",
    ),
    ExampleQuestion(
        question="What does Apple disclose about its share repurchase program?",
        category="text",
        note="Drawn from the notes to the financial statements.",
    ),
    ExampleQuestion(
        question="What images or figures appear in this document?",
        category="figure",
        note="The filing contains one image, the Apple logo. The system should say so rather than invent a chart.",
    ),
]
# Unsupported-question examples were removed from the interface at the user's
# request. The refusal path itself is unchanged: a question the filing does not
# cover still returns refused=true with an explanation, and the behaviour
# remains measured by the five `unsupported` cases in
# tests/evaluation_questions.json.


@lru_cache(maxsize=1)
def _answerer() -> Answerer:
    """Built once per process. Loading the lexical index is not free."""
    return Answerer()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    api = FastAPI(
        title="SEC 10-Q RAG",
        description="Retrieval-augmented question answering over text, tables, and figures.",
        version="0.1.0",
    )

    @api.exception_handler(RAGError)
    async def handle_rag_error(_: Request, exc: RAGError) -> JSONResponse:
        logger.warning("%s: %s", type(exc).__name__, exc.detail or exc.user_message)
        status = {
            EmptyQueryError: 400,
            CollectionMissingError: 409,
            VectorStoreUnavailableError: 503,
            LLMUnavailableError: 503,
        }.get(type(exc), 500)
        return JSONResponse(
            status_code=status,
            content=ErrorResponse(error=exc.user_message).model_dump(),
        )

    @api.post("/api/query", response_model=QueryResponse)
    async def query(request: QueryRequest) -> QueryResponse:
        answer = _answerer().answer(
            request.question,
            top_k=request.top_k,
            content_types=request.content_types,
        )
        return QueryResponse.from_answer(answer)

    @api.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        qdrant = QdrantStore(settings).health()
        key_set = bool(settings.openai_api_key) and not settings.openai_api_key.startswith("sk-replace")
        return HealthResponse(
            ok=bool(qdrant.get("reachable")) and bool(qdrant.get("indexed")) and key_set,
            document_present=settings.resolved_document_path.exists(),
            api_key_configured=key_set,
            llm_model=settings.llm_model,
            vision_model=settings.vision_model,
            embedding_model=settings.embedding_model,
            qdrant=qdrant,
        )

    @api.get("/api/examples", response_model=list[ExampleQuestion])
    async def examples() -> list[ExampleQuestion]:
        return EXAMPLES

    @api.get("/api/pages/{pdf_page}/render")
    async def page_render(pdf_page: int) -> FileResponse:
        path = settings.renders_dir / f"page_{pdf_page:03d}.png"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Page image not available. Run ingestion.")
        return FileResponse(path, media_type="image/png")

    @api.get("/api/figures/{chunk_id}")
    async def figure(chunk_id: str) -> FileResponse:
        # Chunk ids encode page and index: "<doc>::pNNN::figure::I".
        parts = chunk_id.split("::")
        if len(parts) != 4 or parts[2] != "figure":
            raise HTTPException(status_code=400, detail="Not a figure identifier.")

        page = parts[1].lstrip("p")
        matches = sorted(settings.figures_dir.glob(f"p{page}_img{parts[3]}.*"))
        if not matches:
            raise HTTPException(status_code=404, detail="Figure image not available.")
        return FileResponse(matches[0])

    if STATIC_DIR.exists():
        api.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return api
