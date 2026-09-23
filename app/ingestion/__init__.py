"""Loading the source PDF and recovering its page and section structure."""

from app.ingestion.boilerplate import BoilerplateDetector, BoilerplateProfile, normalise
from app.ingestion.loader import LoadedDocument, PageInfo, open_document
from app.ingestion.sections import (
    Heading,
    HeadingKind,
    SectionIndex,
    SectionState,
    SectionTagger,
)

__all__ = [
    "BoilerplateDetector",
    "BoilerplateProfile",
    "Heading",
    "HeadingKind",
    "LoadedDocument",
    "PageInfo",
    "SectionIndex",
    "SectionState",
    "SectionTagger",
    "normalise",
    "open_document",
]
