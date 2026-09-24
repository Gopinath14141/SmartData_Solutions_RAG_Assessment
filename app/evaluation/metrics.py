"""Retrieval and answer metrics.

Every metric here is deterministic. No LLM judge is used, and that is a
deliberate choice rather than an omission — see :mod:`app.evaluation.runner`
for the reasoning and the limitations it leaves behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMERIC = re.compile(r"[,\s]")


def normalise(text: str) -> str:
    """Lowercase and strip thousands separators.

    So that ``82,959``, ``82959``, and ``82 959`` all compare equal. Without
    this the metric would punish a model for formatting a number differently
    from the filing, which is not an error.
    """
    return _NUMERIC.sub("", text.lower())


def contains_all(answer: str, expected: list[str]) -> bool:
    """Whether every expected string appears in the answer."""
    if not expected:
        return True
    haystack = normalise(answer)
    return all(normalise(item) in haystack for item in expected)


@dataclass
class RetrievalScore:
    """Per-question retrieval outcome."""

    hit: bool
    """At least one expected page appears in the retrieved set."""

    recall: float
    """Fraction of expected pages retrieved."""

    precision: float
    """Fraction of retrieved chunks that came from an expected page."""

    reciprocal_rank: float
    """1/rank of the first correct chunk, or 0. Averaged, this is MRR."""

    first_correct_rank: int | None


def score_retrieval(retrieved_pages: list[int], expected_pages: list[int]) -> RetrievalScore:
    """Score one question's retrieval.

    Args:
        retrieved_pages: PDF pages of retrieved chunks, in rank order.
        expected_pages: PDF pages that contain the answer.

    Note:
        Scoring is at page granularity, not chunk granularity. A page can yield
        several chunks and any of them may carry the answer, so demanding a
        specific chunk would measure chunking policy rather than retrieval
        quality. The cost is that precision is optimistic when a page
        contributes many chunks; that limitation is reported rather than hidden.
    """
    if not expected_pages:
        # Unsupported questions have no correct page. Retrieval cannot be
        # scored, and forcing a 0 would drag the averages down for behaviour
        # that is not wrong.
        return RetrievalScore(hit=True, recall=1.0, precision=1.0, reciprocal_rank=1.0, first_correct_rank=None)

    expected = set(expected_pages)
    hits = [page in expected for page in retrieved_pages]

    first = next((index for index, is_hit in enumerate(hits, start=1) if is_hit), None)
    found = expected & set(retrieved_pages)

    return RetrievalScore(
        hit=bool(found),
        recall=len(found) / len(expected),
        precision=(sum(hits) / len(hits)) if hits else 0.0,
        reciprocal_rank=(1.0 / first) if first else 0.0,
        first_correct_rank=first,
    )


@dataclass
class AnswerScore:
    """Per-question answer outcome."""

    correct: bool
    """Contains every expected value, or correctly refused."""

    refusal_correct: bool
    """The refusal decision matched what the question required."""

    hallucinated: bool
    """Answered confidently a question the filing cannot support."""

    citation_correct: bool
    """At least one cited block came from an expected page."""

    cited_any: bool


def score_answer(
    answer_text: str,
    refused: bool,
    cited_pages: list[int],
    expected_pages: list[int],
    expected_contains: list[str],
    should_refuse: bool,
) -> AnswerScore:
    """Score one question's answer."""
    refusal_correct = refused == should_refuse
    hallucinated = should_refuse and not refused

    if should_refuse:
        correct = refused
        citation_correct = True  # Nothing to cite when correctly declining.
    else:
        correct = not refused and contains_all(answer_text, expected_contains)
        citation_correct = (
            bool(set(cited_pages) & set(expected_pages)) if expected_pages and cited_pages else not expected_pages
        )

    return AnswerScore(
        correct=correct,
        refusal_correct=refusal_correct,
        hallucinated=hallucinated,
        citation_correct=citation_correct,
        cited_any=bool(cited_pages),
    )
