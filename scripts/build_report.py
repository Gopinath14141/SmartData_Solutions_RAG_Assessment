"""Render the assessment report to PDF.

Kept as a script rather than done by hand so the PDF is reproducible from the
Markdown source, and so the submitted document cannot drift from the one in
version control.

Markdown is converted to HTML with a print stylesheet, then a headless Chromium
prints it. Chrome and Edge are both already present on any Windows machine and
produce far better typography than the pure-Python PDF writers, which handle
wide tables poorly — and this report is mostly tables.

Usage::

    python scripts/build_report.py
    python scripts/build_report.py --source docs/architecture.md --output out.pdf
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import markdown
except ImportError:  # pragma: no cover - dependency guidance
    print("The 'markdown' package is required: pip install markdown", file=sys.stderr)
    raise SystemExit(1) from None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "docs" / "assessment-report.md"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "assessment-report.pdf"

BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

STYLESHEET = """
@page { size: A4; margin: 18mm 16mm 20mm; }

* { box-sizing: border-box; }

body {
  font-family: "Segoe UI", -apple-system, Helvetica, Arial, sans-serif;
  font-size: 10.2pt;
  line-height: 1.55;
  color: #1a1d21;
  margin: 0;
}

h1 {
  font-size: 21pt;
  line-height: 1.2;
  margin: 0 0 4pt;
  letter-spacing: -0.4pt;
  color: #0f1216;
}
h1 + p { color: #495260; font-size: 11pt; margin: 0 0 4pt; }

h2 {
  font-size: 14pt;
  margin: 22pt 0 7pt;
  padding-bottom: 4pt;
  border-bottom: 1.1pt solid #d3d8de;
  color: #0f1216;
  page-break-after: avoid;
  letter-spacing: -0.2pt;
}
h3 {
  font-size: 11.4pt;
  margin: 14pt 0 5pt;
  color: #23272d;
  page-break-after: avoid;
}

p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 17pt; }
li { margin-bottom: 3pt; }

strong { color: #0b0d10; font-weight: 640; }

code {
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 8.9pt;
  background: #f1f3f6;
  padding: 1pt 3.5pt;
  border-radius: 3pt;
  color: #1f4a7a;
}

pre {
  background: #f7f8fa;
  border: 0.7pt solid #dfe3e8;
  border-left: 2.4pt solid #3b5bdb;
  border-radius: 4pt;
  padding: 8pt 10pt;
  overflow: hidden;
  page-break-inside: avoid;
}
pre code {
  background: none;
  padding: 0;
  color: #23272d;
  font-size: 8.4pt;
  line-height: 1.45;
  white-space: pre-wrap;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 8pt 0 11pt;
  font-size: 9.1pt;
  page-break-inside: avoid;
}
th, td {
  border: 0.6pt solid #d8dce2;
  padding: 4.5pt 7pt;
  text-align: left;
  vertical-align: top;
}
th {
  background: #eef1f5;
  font-weight: 640;
  color: #1a1d21;
}
tbody tr:nth-child(even) { background: #fafbfc; }
/* Numeric columns read better right-aligned; the source marks them with an
   alignment row, which python-markdown turns into an inline style. */
td[style*="right"], th[style*="right"] { text-align: right; font-variant-numeric: tabular-nums; }

blockquote {
  margin: 8pt 0;
  padding: 7pt 12pt;
  border-left: 2.4pt solid #b9c2cf;
  background: #f7f8fa;
  color: #33383f;
  page-break-inside: avoid;
}
blockquote p:last-child { margin-bottom: 0; }

hr {
  border: 0;
  border-top: 0.7pt solid #dfe3e8;
  margin: 16pt 0;
}

a { color: #2743b0; text-decoration: none; }
"""


def find_browser() -> str:
    """Locate a Chromium-family browser."""
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("chrome", "msedge", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError(
        "No Chromium-family browser found. Install Chrome or Edge, or convert "
        "docs/assessment-report.md with any Markdown-to-PDF tool."
    )


def render_html(source: Path) -> str:
    """Convert Markdown to a standalone, print-ready HTML document."""
    body = markdown.markdown(
        source.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{source.stem}</title><style>{STYLESHEET}</style></head>"
        f"<body>{body}</body></html>"
    )


def build(source: Path, output: Path) -> Path:
    """Render ``source`` to a PDF at ``output``."""
    if not source.exists():
        raise FileNotFoundError(source)

    browser = find_browser()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as workspace:
        html_path = Path(workspace) / "report.html"
        html_path.write_text(render_html(source), encoding="utf-8")

        # Fixed argument list, no shell: nothing here is user-controlled.
        result = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-pdf-header-footer",
                f"--print-to-pdf={output}",
                html_path.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    if not output.exists():
        raise RuntimeError(
            f"{Path(browser).name} did not produce a PDF.\n{result.stderr[:800]}"
        )

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        written = build(args.source, args.output)
    except Exception as exc:  # top-level script boundary: report, do not traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {written} ({written.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
