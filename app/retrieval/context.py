"""Assemble retrieved chunks into evidence for the generation model.

Evidence is grouped by content type, labelled with provenance, and capped by a
character budget. The model must be able to cite what it used, so every block
carries its own identifier and page (docs/architecture.md §8).

Retrieved document text is untrusted input. It is fenced with explicit markers
and the system prompt states that text inside them is data to be quoted, never
instructions to follow. For an SEC filing the practical risk is negligible, but
the ingestion path accepts arbitrary PDFs, so the mitigation belongs in the
architecture rather than in a note (§8).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.models import ContentType
from app.retrieval.fusion import RetrievedChunk

_EVIDENCE_OPEN = "<<<EVIDENCE"
_EVIDENCE_CLOSE = "EVIDENCE>>>"

_TYPE_ORDER = (ContentType.TABLE, ContentType.TEXT, ContentType.FIGURE)
"""Tables first: on a financial filing they answer the most specific questions."""


@dataclass
class BuiltContext:
    """Formatted evidence and what went into it."""

    text: str
    used: list[RetrievedChunk]
    dropped: int
    characters: int

    @property
    def is_empty(self) -> bool:
        return not self.used


class ContextBuilder:
    """Turns ranked chunks into a prompt-ready evidence block."""

    def __init__(self, settings: Settings | None = None, budget_chars: int = 12000) -> None:
        self.settings = settings or get_settings()
        self.budget_chars = budget_chars

    def build(self, results: list[RetrievedChunk]) -> BuiltContext:
        """Assemble evidence, deduplicating and respecting the budget."""
        seen: set[str] = set()
        unique: list[RetrievedChunk] = []
        for entry in results:
            # Deduplicate on content as well as id: the same figures appear in
            # Note 9 and again in MD&A, and two copies waste budget without
            # adding evidence. Provenance of the kept copy is what gets cited.
            fingerprint = entry.chunk.display_text.strip()[:400]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(entry)

        ordered = sorted(
            unique,
            key=lambda entry: (_TYPE_ORDER.index(entry.chunk.content_type), entry.rank),
        )

        blocks: list[str] = []
        used: list[RetrievedChunk] = []
        total = 0

        for entry in ordered:
            block = self._format(entry)
            if total + len(block) > self.budget_chars and used:
                break
            blocks.append(block)
            used.append(entry)
            total += len(block)

        body = "\n\n".join(blocks)
        text = f"{_EVIDENCE_OPEN}\n{body}\n{_EVIDENCE_CLOSE}" if body else ""

        return BuiltContext(
            text=text,
            used=used,
            dropped=len(ordered) - len(used),
            characters=len(text),
        )

    def _format(self, entry: RetrievedChunk) -> str:
        chunk = entry.chunk
        provenance = chunk.provenance

        header = (
            f"[{chunk.chunk_id}] "
            f"type={chunk.content_type.value} | {provenance.citation} | {provenance.location}"
        )

        lines = [header]

        table = chunk.table
        if table is not None:
            if table.units:
                lines.append(f"Units: {table.units}")
            if not table.is_trustworthy:
                lines.append(
                    "WARNING: this table's structure could not be verified "
                    f"({table.validation.value}). Do not quote individual cells as exact; "
                    "say so if the question depends on them."
                )

        figure = chunk.figure
        if figure is not None and figure.is_decorative:
            lines.append(
                "NOTE: this image is decorative (a logo or rule), not a data figure. "
                "It carries no values to interpret."
            )

        lines.append(chunk.display_text.strip())
        return "\n".join(lines)
