"""Reciprocal Rank Fusion of dense and lexical result lists.

RRF is used rather than a weighted blend of scores because cosine similarity
and BM25 occupy different, corpus-dependent scales. Blending them needs a
normalisation constant tuned per corpus — one more thing to justify and to get
wrong — while RRF uses only rank position and needs no tuning (D5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Chunk, ContentType


@dataclass
class RetrievedChunk:
    """A chunk and how it was found.

    The per-retriever ranks are kept because they are genuinely diagnostic: a
    chunk found only by BM25 is usually an exact-token match such as a figure or
    a date, and one found only by embeddings is usually a paraphrase.
    """

    chunk: Chunk
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None
    boost: float = 0.0
    rank: int = field(default=0)

    @property
    def found_by(self) -> str:
        if self.dense_rank is not None and self.lexical_rank is not None:
            return "both"
        if self.dense_rank is not None:
            return "dense"
        return "lexical"


def reciprocal_rank_fusion(
    dense: list[tuple[Chunk, float]],
    lexical: list[tuple[Chunk, float]],
    k: int = 60,
    bias: dict[ContentType, float] | None = None,
    boost_weight: float = 0.0,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists into one.

    Args:
        dense: Vector search results, best first.
        lexical: BM25 results, best first.
        k: RRF constant. Larger values flatten the contribution of top ranks.
        bias: Per-content-type preference from the router.
        boost_weight: How strongly to apply that bias.

    Note:
        RRF scores are compressed: at ``k=60``, rank 1 scores ``1/61`` and rank
        10 scores ``1/70`` — barely 13% apart. A boost therefore moves a chunk
        further than intuition suggests. A boosted chunk at rank *r* overtakes
        rank 1 when ``r < 1 + (k + 1) * boost_weight``, so at ``k=60`` a weight
        of 0.05 is worth roughly three rank positions and 0.15 would be worth
        ten. The default is set accordingly; see ``ROUTER_BOOST`` in
        ``.env.example``.

    Returns:
        Fused results, best first, each carrying its rank.
    """
    merged: dict[str, RetrievedChunk] = {}

    for position, (chunk, _) in enumerate(dense, start=1):
        merged[chunk.chunk_id] = RetrievedChunk(
            chunk=chunk, score=1.0 / (k + position), dense_rank=position
        )

    for position, (chunk, _) in enumerate(lexical, start=1):
        contribution = 1.0 / (k + position)
        existing = merged.get(chunk.chunk_id)
        if existing is None:
            merged[chunk.chunk_id] = RetrievedChunk(
                chunk=chunk, score=contribution, lexical_rank=position
            )
        else:
            existing.score += contribution
            existing.lexical_rank = position

    if bias and boost_weight:
        for entry in merged.values():
            weight = bias.get(entry.chunk.content_type, 0.0)
            if weight:
                # Scaled by the entry's own score, so the boost is proportional
                # and bounded — it reorders within a window of a few ranks
                # rather than promoting an unrelated chunk to the top.
                entry.boost = entry.score * weight * boost_weight
                entry.score += entry.boost

    ordered = sorted(merged.values(), key=lambda entry: entry.score, reverse=True)
    for position, entry in enumerate(ordered, start=1):
        entry.rank = position
    return ordered
