"""Query routing, hybrid retrieval, fusion, and context assembly."""

from app.retrieval.context import BuiltContext, ContextBuilder
from app.retrieval.fusion import RetrievedChunk, reciprocal_rank_fusion
from app.retrieval.hybrid import HybridRetriever, RetrievalResult
from app.retrieval.router import describe, route

__all__ = [
    "BuiltContext",
    "ContextBuilder",
    "HybridRetriever",
    "RetrievalResult",
    "RetrievedChunk",
    "describe",
    "reciprocal_rank_fusion",
    "route",
]
