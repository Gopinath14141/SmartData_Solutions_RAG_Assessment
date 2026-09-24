"""Rendering answers and their sources for text output.

The printed page leads every citation because that is the number a reader sees
on the page and the number the filing's own table of contents uses. The PDF
index follows in parentheses for unambiguous lookup. In this document the two
differ by three, so presenting only one of them — or the wrong one — puts every
citation three pages out (docs/architecture.md §9).
"""

from __future__ import annotations

from app.generation.answerer import Answer, Evidence


def format_sources(evidence: list[Evidence], only_cited: bool = True) -> str:
    """Render a source list.

    Args:
        evidence: Evidence attached to an answer.
        only_cited: Restrict to blocks the model said it relied on. When it
            cited nothing, all retrieved evidence is shown instead, so the
            reader can still see what the answer was drawn from.
    """
    selected = [item for item in evidence if item.cited_by_model] if only_cited else list(evidence)
    if only_cited and not selected:
        selected = list(evidence)

    if not selected:
        return "Sources: none — no supporting evidence was retrieved."

    lines = ["Sources:"]
    for item in sorted(selected, key=lambda e: (e.pdf_page, e.rank)):
        note = ""
        if item.content_type == "table" and not item.table_trustworthy:
            note = "  [structure unverified]"
        elif item.content_type == "figure" and item.figure_decorative:
            note = "  [decorative image]"
        lines.append(f"  - {item.citation} — {item.content_type} — {item.section}{note}")
    return "\n".join(lines)


def render_answer(answer: Answer, show_telemetry: bool = True) -> str:
    """Render a complete answer with sources and optional telemetry."""
    parts: list[str] = []

    if answer.refused:
        parts.append("No supported answer found.")
        parts.append("")
    parts.append(answer.text)
    parts.append("")
    parts.append(format_sources(answer.evidence))

    if show_telemetry:
        mix = ", ".join(f"{k}={v}" for k, v in sorted(answer.content_mix.items())) or "none"
        parts.append("")
        parts.append(
            f"[{answer.model} | routed: {answer.routed_to} | vision: {answer.used_vision} | "
            f"{answer.chunks_retrieved} chunks ({mix}) | "
            f"retrieve {answer.seconds_retrieve:.2f}s, generate {answer.seconds_generate:.2f}s]"
        )

    return "\n".join(parts)
