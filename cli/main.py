"""Non-interactive command-line interface for the local research platform."""

from __future__ import annotations

import json
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from tradingagents import AnalysisRequest, RunProfile, TradingAgents
from tradingagents.application.contracts import RunEvent, RunStatus
from tradingagents.application.legacy import LegacyMemoryImporter
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.application.worker import AnalysisWorker
from tradingagents.dataflows.symbol_utils import market_today
from tradingagents.version import __version__
from tradingagents.web import create_app

PROJECT_DESCRIPTION = "Local evidence-first investment research platform"

app = typer.Typer(
    name="tradingagents",
    help=PROJECT_DESCRIPTION,
    no_args_is_help=True,
    add_completion=True,
)
runs_app = typer.Typer(help="Inspect and control durable research runs.")
memory_app = typer.Typer(help="Import legacy decision memory.")
db_app = typer.Typer(help="Maintain the local SQLite database.")
app.add_typer(runs_app, name="runs")
app.add_typer(memory_app, name="memory")
app.add_typer(db_app, name="db")

console = Console()
event_console = Console(stderr=True)
_ANALYSTS = ("market", "social", "news", "fundamentals")


class ExportFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Run research, serve the Web UI, or manage durable local runs."""


@app.command("run")
def run_command(
    ticker: Annotated[
        str,
        typer.Argument(help="Instrument symbol, for example 7203.T."),
    ],
    analysis_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Point-in-time cutoff (YYYY-MM-DD); defaults to the market-local date.",
        ),
    ] = None,
    profile: Annotated[RunProfile, typer.Option("--profile")] = RunProfile.STANDARD,
    analysts: Annotated[
        str,
        typer.Option("--analysts", help="Comma-separated analyst keys."),
    ] = ",".join(_ANALYSTS),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    quick_model: Annotated[str | None, typer.Option("--quick-model")] = None,
    deep_model: Annotated[str | None, typer.Option("--deep-model")] = None,
    quick_reasoning: Annotated[
        str | None, typer.Option("--quick-reasoning")
    ] = None,
    deep_reasoning: Annotated[
        str | None, typer.Option("--deep-reasoning")
    ] = None,
    output_language: Annotated[
        str | None,
        typer.Option(
            "--output-language",
            help=(
                "Report language (en, zh-CN, ja, or a custom instruction); "
                "defaults to application config."
            ),
        ),
    ] = None,
    provenance: Annotated[
        bool | None,
        typer.Option("--provenance/--no-provenance"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the typed result as JSON."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress progress events."),
    ] = False,
) -> None:
    """Execute one analysis synchronously and persist it in SQLite."""
    selected = _parse_analysts(analysts)
    cutoff = (
        _parse_analysis_date(analysis_date)
        if analysis_date
        else _market_local_date(ticker)
    )
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=cutoff,
        profile=profile,
        analysts=selected,
        llm_provider=provider,
        quick_model=quick_model,
        deep_model=deep_model,
        quick_reasoning_effort=quick_reasoning,
        deep_reasoning_effort=deep_reasoning,
        output_language=output_language,
        provenance=provenance,
    )
    application = _application()
    try:
        result = application.run(
            request,
            on_event=None if quiet else _print_event,
        )
    except Exception as exc:
        event_console.print(
            f"[red]Analysis failed ({type(exc).__name__}). "
            "Inspect the local server log or run record.[/red]"
        )
        raise typer.Exit(code=1) from None
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(
        f"[green]Run {result.run_id} {result.status.value}[/green] "
        f"for [bold]{result.instrument}[/bold]"
    )
    if result.decision:
        console.print(
            f"Rating: [bold]{result.decision.rating.value}[/bold] · "
            f"confidence {result.decision.confidence:.0%}"
        )


@app.command()
def serve(
    log_level: Annotated[str, typer.Option("--log-level")] = "info",
) -> None:
    """Serve the Web run center using the configured loopback or LAN policy."""
    settings = _settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=log_level,
    )


@app.command()
def worker(
    once: Annotated[
        bool,
        typer.Option("--once", help="Process at most one queue item."),
    ] = False,
) -> None:
    """Run the single-concurrency analysis and outcome-settlement worker."""
    process = AnalysisWorker(_settings())
    if once:
        worked = process.run_once()
        console.print("Processed one run." if worked else "Queue is empty.")
        return
    process.serve_forever()


@runs_app.command("list")
def list_runs(
    status: Annotated[RunStatus | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List persisted runs, newest first."""
    items = _service().repository.list_runs(
        status=status,
        limit=limit,
        offset=offset,
    )
    if json_output:
        _echo_json([item.model_dump(mode="json") for item in items])
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Run ID")
    table.add_column("Ticker")
    table.add_column("Date")
    table.add_column("Profile")
    table.add_column("Status")
    table.add_column("Updated")
    for item in items:
        table.add_row(
            item.id,
            item.request.ticker,
            item.request.analysis_date.isoformat(),
            item.request.profile.value,
            item.status.value,
            item.updated_at.isoformat(timespec="seconds"),
        )
    console.print(table)


@runs_app.command()
def show(run_id: str) -> None:
    """Show one persisted run and any available typed result."""
    service = _service()
    try:
        run = service.repository.get_run(run_id)
        result = (
            service.repository.get_result(run_id)
            if run.status is RunStatus.SUCCEEDED
            else None
        )
    except Exception as exc:
        _lifecycle_error(exc)
    _echo_json(
        {
            "run": run.model_dump(mode="json"),
            "result": result.model_dump(mode="json") if result else None,
        }
    )


@runs_app.command()
def cancel(run_id: str) -> None:
    """Request cooperative cancellation at the next graph node boundary."""
    _print_lifecycle(lambda service: service.cancel(run_id))


@runs_app.command()
def retry(run_id: str) -> None:
    """Queue a new attempt for a failed run, reusing a compatible checkpoint."""
    _print_lifecycle(lambda service: service.retry(run_id))


@runs_app.command()
def rerun(run_id: str) -> None:
    """Create a linked run with a fresh data snapshot."""
    _print_lifecycle(lambda service: service.rerun(run_id))


@memory_app.command("import")
def import_memory(
    source: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True),
    ],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Write importable blocks; the default is a dry run.",
        ),
    ] = False,
    backup: Annotated[
        bool,
        typer.Option(
            "--backup/--no-backup",
            help="Back up the source before an applied import.",
        ),
    ] = True,
) -> None:
    """Dry-run or import legacy Markdown decision memory idempotently."""
    service = _service()
    importer = LegacyMemoryImporter(service.settings, service.repository)
    try:
        report = importer.import_file(
            source,
            dry_run=not apply,
            create_backup=backup,
        )
    except (OSError, ValueError) as exc:
        event_console.print(f"[red]Memory import failed: {exc}[/red]")
        raise typer.Exit(code=1) from None
    typer.echo(report.model_dump_json(indent=2))
    if report.malformed:
        raise typer.Exit(code=2)


@app.command()
def export(
    run_id: str,
    format: Annotated[
        ExportFormat,
        typer.Option("--format"),
    ] = ExportFormat.MARKDOWN,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False),
    ] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Export a completed run as Markdown or JSON."""
    try:
        _media_type, content = _service().export(run_id, format=format.value)
    except Exception as exc:
        _lifecycle_error(exc)
    if output is None:
        typer.echo(content)
        return
    _write_text(output, content, force=force)
    console.print(f"Wrote {output.expanduser().resolve()}")


@db_app.command("backup")
def backup_database(
    destination: Annotated[Path, typer.Argument(dir_okay=False)],
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Create a consistent SQLite backup without stopping Web or worker."""
    target = destination.expanduser().resolve()
    if target.exists() and not force:
        event_console.print(f"[red]Refusing to overwrite existing file: {target}[/red]")
        raise typer.Exit(code=1)
    try:
        created = _service().backup_database(target)
    except (OSError, ValueError) as exc:
        event_console.print(f"[red]Database backup failed: {exc}[/red]")
        raise typer.Exit(code=1) from None
    console.print(f"Backup created at {created}")


def _settings() -> AppSettings:
    return AppSettings.from_env()


def _service() -> AnalysisService:
    return AnalysisService(_settings())


def _application() -> TradingAgents:
    return TradingAgents(_settings())


def _parse_analysts(raw: str) -> tuple[str, ...]:
    selected = tuple(
        dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip())
    )
    invalid = [name for name in selected if name not in _ANALYSTS]
    if invalid:
        raise typer.BadParameter(
            f"unknown analyst(s): {', '.join(invalid)}; "
            f"choose from {', '.join(_ANALYSTS)}",
            param_hint="--analysts",
        )
    if not selected:
        raise typer.BadParameter(
            "at least one analyst is required",
            param_hint="--analysts",
        )
    return tuple(name for name in _ANALYSTS if name in selected)


def _parse_analysis_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise typer.BadParameter(
            "expected YYYY-MM-DD",
            param_hint="--date",
        ) from None


def _market_local_date(ticker: str) -> date:
    try:
        return market_today(ticker)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="ticker") from None


def _print_event(event: RunEvent) -> None:
    node = f" · {event.node}" if event.node else ""
    event_console.print(
        f"[dim]#{event.sequence}[/dim] {event.event_type}{node}"
    )


def _print_lifecycle(action) -> None:
    try:
        view = action(_service())
    except Exception as exc:
        _lifecycle_error(exc)
    _echo_json(view.model_dump(mode="json"))


def _lifecycle_error(exc: Exception) -> None:
    event_console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
    raise typer.Exit(code=1) from None


def _echo_json(value: Any) -> None:
    typer.echo(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def _write_text(path: Path, content: str, *, force: bool) -> None:
    destination = path.expanduser().resolve()
    if destination.exists() and not force:
        event_console.print(
            f"[red]Refusing to overwrite existing file: {destination}[/red]"
        )
        raise typer.Exit(code=1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    app()
