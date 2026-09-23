"""Centralised configuration, bound to environment variables and ``.env``.

Plan §20: every tunable lives here. No module hardcodes a model name, a chunk
size, a retrieval constant, or a path. Defaults match ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ConfigurationError, MissingAPIKeyError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings.

    Values resolve in the usual pydantic-settings order: environment variable,
    then ``.env``, then the default declared here.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── OpenAI ────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)

    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=1200, gt=0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0)

    # ── Qdrant ────────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = Field(default=6333, gt=0, lt=65536)
    qdrant_collection: str = "aapl_10q_2022_q3"
    qdrant_timeout_seconds: float = Field(default=30.0, gt=0)
    qdrant_startup_retries: int = Field(default=10, ge=0)
    qdrant_startup_backoff_seconds: float = Field(default=2.0, gt=0)

    # ── Document ──────────────────────────────────────────────────────────
    document_id: str = "AAPL_2022_Q3_10Q"
    document_path: Path = Path("data/raw/2022_Q3_AAPL.pdf")
    document_url: str = (
        "https://raw.githubusercontent.com/docugami/KG-RAG-datasets/main"
        "/sec-10-q/data/v1/docs/2022%20Q3%20AAPL.pdf"
    )

    # ── Chunking (docs/architecture.md §6) ────────────────────────────────
    text_chunk_size: int = Field(default=900, gt=0)
    text_chunk_overlap: int = Field(default=150, ge=0)

    # ── Retrieval (docs/architecture.md §7) ───────────────────────────────
    retrieval_top_k: int = Field(default=8, gt=0)
    dense_top_k: int = Field(default=20, gt=0)
    bm25_top_k: int = Field(default=20, gt=0)
    rrf_k: int = Field(default=60, gt=0)
    router_boost: float = Field(default=0.15, ge=0.0)

    # ── Rendering ─────────────────────────────────────────────────────────
    page_render_dpi: int = Field(default=150, gt=0)
    figure_min_dimension: int = Field(
        default=32,
        gt=0,
        description="Images smaller than this in both dimensions are treated as decorative.",
    )

    # ── Serving ───────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, gt=0, lt=65536)
    log_level: str = "INFO"

    # ── Derived paths ─────────────────────────────────────────────────────

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def resolved_document_path(self) -> Path:
        """``document_path`` as an absolute path, relative to the project root.

        Keeps the configured value portable — no machine-specific absolute paths
        in ``.env`` (plan §25).
        """
        path = self.document_path
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def figures_dir(self) -> Path:
        return self.data_dir / "figures"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def eval_runs_dir(self) -> Path:
        return self.data_dir / "eval_runs"

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    # ── Validation ────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.text_chunk_overlap >= self.text_chunk_size:
            raise ConfigurationError(
                "TEXT_CHUNK_OVERLAP must be smaller than TEXT_CHUNK_SIZE.",
                detail=f"overlap={self.text_chunk_overlap} size={self.text_chunk_size}",
            )
        if self.retrieval_top_k > min(self.dense_top_k, self.bm25_top_k):
            raise ConfigurationError(
                "RETRIEVAL_TOP_K cannot exceed DENSE_TOP_K or BM25_TOP_K — "
                "fusion would have fewer candidates than it is asked to return.",
                detail=(
                    f"top_k={self.retrieval_top_k} dense={self.dense_top_k} "
                    f"bm25={self.bm25_top_k}"
                ),
            )
        return self

    def require_api_key(self) -> str:
        """Return the API key, or raise with a remediation message.

        Called at startup so a missing key fails before a user types a question,
        not after (plan §17).
        """
        if not self.openai_api_key or self.openai_api_key.startswith("sk-replace"):
            raise MissingAPIKeyError()
        return self.openai_api_key

    def ensure_directories(self) -> None:
        """Create the generated-artefact directories if they do not exist."""
        for directory in (
            self.processed_dir,
            self.figures_dir,
            self.renders_dir,
            self.eval_runs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton settings instance.

    Cached so that ``.env`` is read once per process and every module observes
    the same configuration.
    """
    return Settings()
