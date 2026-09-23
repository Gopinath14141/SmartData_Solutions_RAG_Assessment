"""Fetch the source 10-Q.

The PDF is not committed (see ``.gitignore``): it is a public SEC filing with a
stable URL, so a download script keeps the repository small and makes the
provenance of the document explicit rather than implicit in a binary blob.

Usage::

    python scripts/download_document.py
    python scripts/download_document.py --force   # re-download over an existing file
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings

PDF_MAGIC = b"%PDF-"


def download(force: bool = False) -> Path:
    """Download the configured source document.

    Args:
        force: Re-download even if the file already exists.

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If the downloaded bytes are not a PDF.
    """
    settings = get_settings()
    destination = settings.resolved_document_path

    if destination.exists() and not force:
        print(f"Already present: {destination} ({destination.stat().st_size:,} bytes)")
        print("Use --force to re-download.")
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {settings.document_url}")

    with urllib.request.urlopen(settings.document_url) as response:
        payload = response.read()

    if not payload.startswith(PDF_MAGIC):
        raise RuntimeError(
            f"Downloaded content is not a PDF (first bytes: {payload[:8]!r}). "
            "Check DOCUMENT_URL in .env."
        )

    destination.write_bytes(payload)
    print(f"Saved {destination} ({len(payload):,} bytes)")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-download if the file exists.")
    args = parser.parse_args()

    try:
        download(force=args.force)
    except Exception as exc:  # top-level script boundary: report, do not traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
