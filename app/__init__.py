"""RAG information-retrieval system over the Apple Inc. Q3 FY2022 Form 10-Q.

Pipeline stages live in sibling packages and depend only on ``app.models`` and
``app.config``; no extraction or retrieval module knows which LLM provider is in
use (docs/architecture.md §11).
"""

__version__ = "0.1.0"
