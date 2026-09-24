"""Command-line interface.

The reproducible path for ingestion, querying, and evaluation. It shares the
service layer with the HTTP API, so both exercise identical code
(docs/design-decisions.md D9).
"""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.config import get_settings
from app.errors import RAGError
from app.generation import Answerer, render_answer
from app.indexing import QdrantStore, build_index
from app.models import ContentType

console = Console()
app = typer.Typer(
    add_completion=False,
    help="RAG information retrieval over the Apple Q3 FY2022 Form 10-Q.",
)


def _configure_logging(verbose: bool) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.DEBUG if verbose else getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def ingest(
    rebuild: bool = typer.Option(False, "--rebuild", help="Drop and recreate the index first."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Extract, chunk, embed, and index the document."""
    _configure_logging(verbose)
    try:
        report = build_index(rebuild=rebuild)
    except RAGError as exc:
        console.print(f"[red]{exc.user_message}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Ingestion", show_header=False, box=None)
    table.add_row("Pages", str(report.pages))
    table.add_row("Chunks", f"{report.total_chunks} "
                            f"(text {report.text_chunks}, table {report.table_chunks}, "
                            f"figure {report.figure_chunks})")
    table.add_row("Indexed", str(report.indexed))
    table.add_row("Tables OK", f"{report.tables_ok}/{report.tables_ok + sum(report.tables_flagged.values())}")
    if report.tables_flagged:
        table.add_row("Tables flagged", ", ".join(f"{k}={v}" for k, v in report.tables_flagged.items()))
    table.add_row("Columns", f"{report.raw_columns} -> {report.collapsed_columns}")
    table.add_row("Captions / units", f"{report.captions_attached} / {report.units_attached}")
    table.add_row("Figures", f"{report.figures_total} ({report.figures_decorative} decorative)")
    table.add_row(
        "Timing",
        f"extract {report.seconds_extract:.1f}s, embed {report.seconds_embed:.1f}s, "
        f"index {report.seconds_index:.1f}s, total {report.seconds_total:.1f}s",
    )
    console.print(table)


@app.command()
def query(
    question: str = typer.Argument(..., help="The question to ask."),
    top_k: int = typer.Option(None, "--top-k", "-k", help="Chunks to retrieve."),
    content_type: list[str] = typer.Option(
        None, "--type", "-t", help="Restrict to text, table, or figure. Repeatable."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Ask a question about the document."""
    _configure_logging(verbose)

    types = None
    if content_type:
        try:
            types = [ContentType(value.lower()) for value in content_type]
        except ValueError as exc:
            console.print("[red]--type must be one of: text, table, figure[/red]")
            raise typer.Exit(code=1) from exc

    try:
        answer = Answerer().answer(question, top_k=top_k, content_types=types)
    except RAGError as exc:
        console.print(f"[red]{exc.user_message}[/red]")
        raise typer.Exit(code=1) from exc

    border = "yellow" if answer.refused else "green"
    console.print(Panel(render_answer(answer), title=question, border_style=border))


@app.command()
def status(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Report index and service health."""
    _configure_logging(verbose)
    settings = get_settings()

    table = Table(title="Status", show_header=False, box=None)
    table.add_row("Document", str(settings.resolved_document_path))
    table.add_row("Document present", "yes" if settings.resolved_document_path.exists() else "no")
    table.add_row("Qdrant", settings.qdrant_url)
    table.add_row("Text model", settings.llm_model)
    table.add_row("Vision model", settings.vision_model)
    table.add_row("Embedding model", settings.embedding_model)
    table.add_row("API key set", "yes" if settings.openai_api_key and not settings.openai_api_key.startswith("sk-replace") else "no")

    health = QdrantStore(settings).health()
    for key, value in health.items():
        table.add_row(f"qdrant.{key}", str(value))

    console.print(table)


@app.command()
def evaluate(
    output: str = typer.Option(None, "--output", "-o", help="Where to write the results JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the evaluation set and report retrieval and answer metrics."""
    _configure_logging(verbose)
    from app.evaluation import run_evaluation

    try:
        results = run_evaluation(output_path=output)
    except RAGError as exc:
        console.print(f"[red]{exc.user_message}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(results.render())


def _port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    """Whether the server could bind this port on this host.

    Two details matter and both were wrong in the first version:

    * ``SO_REUSEADDR`` must **not** be set. On Windows it permits binding an
      address that is already in use, so the probe succeeds and the check
      reports a busy port as free.
    * The probe must use the host the server will actually bind. Binding
      ``127.0.0.1`` succeeds while another process holds ``0.0.0.0`` on the
      same port, which again hides the clash.
    """
    import socket

    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    for candidate in {host, probe_host}:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((candidate, port))
        except OSError:
            return False
    return True


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the web interface and HTTP API."""
    import uvicorn

    settings = get_settings()
    chosen = port or settings.api_port
    bind_host = host or settings.api_host

    # Checked before handing off to uvicorn, which reports a port clash as a
    # bare OSError with a Windows error number and no suggested remedy.
    if not _port_is_free(chosen, bind_host):
        alternatives = [
            p for p in (8010, 8080, 8100, 8200, 9000, 9090) if _port_is_free(p, bind_host)
        ]
        console.print(f"[red]Port {chosen} is already in use by another program.[/red]")
        if alternatives:
            console.print(
                f"Try: [bold]python -m app.cli serve --port {alternatives[0]}[/bold]"
                f"   (also free: {', '.join(str(p) for p in alternatives[1:4])})"
            )
        else:
            console.print("Pass a free port with --port, or stop whatever is using it.")
        raise typer.Exit(code=1)

    console.print(f"Serving on [bold]http://localhost:{chosen}[/bold]  (Ctrl+C to stop)")
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=bind_host,
        port=chosen,
        reload=reload,
    )


if __name__ == "__main__":
    app()
