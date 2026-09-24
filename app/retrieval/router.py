"""Bias retrieval toward the content type a question is likely to need.

Deliberately a *soft* bias, never a filter (docs/architecture.md §7). A
misrouted hard filter excludes the correct evidence and the failure is
unrecoverable; a misrouted soft bias only reorders, and fusion still surfaces
the right chunk. Plan §10 invites routing, and this is the form of it that
cannot make the system worse than not routing at all.

Rules rather than a model: the cues are few, obvious, and free to evaluate, and
a classifier would add a network call and a failure mode to a decision worth a
fraction of a rank position.
"""

from __future__ import annotations

import re

from app.models import ContentType

_FIGURE_CUES = re.compile(
    r"\b(figure|fig\.|chart|graph|diagram|image|picture|photo|illustration|logo|visual)\b",
    re.IGNORECASE,
)

_TABLE_CUES = re.compile(
    r"""(
        \b(how\s+much|how\s+many|total|amount|revenue|sales|income|margin|expense|
           cost|cash|assets?|liabilities|equity|earnings|eps|tax|debt|segment|
           compare|comparison|change|increase|decrease|percentage|percent|
           quarter|quarterly|year[-\s]?over[-\s]?year)\b
        | \$ | %
        | \b\d{1,3}(,\d{3})+\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_TEXT_CUES = re.compile(
    r"\b(describe|explain|what\s+does|why|policy|risk|discussion|state[sd]?|"
    r"disclose[sd]?|mention|say|states?|commitment|litigation|proceeding)\b",
    re.IGNORECASE,
)


def route(query: str) -> dict[ContentType, float]:
    """Return a per-content-type bias in the range 0..1.

    A type with no cue receives 0, meaning "no boost" rather than "exclude".
    """
    bias: dict[ContentType, float] = {
        ContentType.TEXT: 0.0,
        ContentType.TABLE: 0.0,
        ContentType.FIGURE: 0.0,
    }

    if _FIGURE_CUES.search(query):
        bias[ContentType.FIGURE] = 1.0
    if _TABLE_CUES.search(query):
        bias[ContentType.TABLE] = 1.0
    if _TEXT_CUES.search(query):
        bias[ContentType.TEXT] = 1.0

    # A question with no recognisable cue should not be nudged anywhere.
    if not any(bias.values()):
        return dict.fromkeys(bias, 0.0)

    return bias


def describe(query: str) -> str:
    """Human-readable routing decision, surfaced in API telemetry."""
    bias = route(query)
    preferred = [content_type.value for content_type, weight in bias.items() if weight > 0]
    return ", ".join(preferred) if preferred else "no preference"
