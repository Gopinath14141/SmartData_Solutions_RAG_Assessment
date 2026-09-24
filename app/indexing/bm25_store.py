"""Lexical retrieval over the chunk corpus.

Hybrid retrieval is not decoration on a financial filing. Queries are dense with
exact tokens — ``Note 5``, ``June 25, 2022``, ``16,070,752,000``, ``iPhone`` —
where embeddings are weakest and where a near miss returns the wrong number
rather than a slightly worse paragraph. BM25 is precise on exactly these.

The index is rebuilt in memory from the stored chunks, which takes milliseconds
at this corpus size and removes a persistence format to keep in sync.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from app.models import Chunk, ContentType

_TOKEN = re.compile(r"[a-z0-9][a-z0-9,\.]*", re.IGNORECASE)


def _singular(token: str) -> str | None:
    """A crude singular form, or None if the token is already singular.

    Deliberately minimal — not a stemmer. BM25 has no morphology of its own, so
    without this a question asking about "images" or "figures" never matches a
    chunk describing an "image" or a "figure", and the figure pathway fails on
    the most natural phrasing of a figure question. Full stemming would also
    conflate financial terms that ought to stay distinct, so only regular
    plurals are handled.
    """
    if len(token) < 4 or not token.isalpha():
        return None
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return None


def tokenize(text: str) -> list[str]:
    """Split text into lexical tokens.

    Commas and decimal points are kept inside tokens so that ``82,959`` stays a
    single searchable term rather than becoming ``82`` and ``959``. Both the
    bare and comma-stripped forms are emitted, so a query written ``82959``
    still matches a document written ``82,959``. Regular plurals additionally
    emit their singular form.
    """
    tokens: list[str] = []
    for match in _TOKEN.finditer(text.lower()):
        token = match.group(0).strip(".,")
        if not token:
            continue
        tokens.append(token)

        stripped = token.replace(",", "")
        if stripped != token and stripped:
            tokens.append(stripped)

        singular = _singular(token)
        if singular:
            tokens.append(singular)
    return tokens


class BM25Store:
    """In-memory lexical index over chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._corpus = [tokenize(chunk.embed_text) for chunk in chunks]
        # BM25Okapi raises on an empty corpus; an empty index searches to nothing.
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self,
        query: str,
        limit: int,
        content_types: list[ContentType] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Top matches by BM25 score."""
        if self._bm25 is None:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        allowed = set(content_types) if content_types else None

        ranked = sorted(
            (
                (chunk, float(score))
                for chunk, score in zip(self.chunks, scores, strict=True)
                if score > 0 and (allowed is None or chunk.content_type in allowed)
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked[:limit]
