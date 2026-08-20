"""Non-interactive command-line interface for the local research platform."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from enum import Enum, StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from tradingagents import AnalysisRequest, RunProfile, TradingAgents
from tradingagents.application.contracts import RunEvent, RunStatus
from tradingagents.application.errors import (
    InstrumentEligibilityUnavailableError,
    UnsupportedInstrumentError,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.application.worker import AnalysisWorker
from tradingagents.dataflows.symbol_utils import market_today
from tradingagents.version import __version__
from tradingagents.web import create_app
from tradingagents.web.access_logging import uvicorn_log_config

from .supervisor import ColorMode, LocalProcessSupervisor

PROJECT_DESCRIPTION = "Local evidence-first investment research platform"

app = typer.Typer(
    name="tradingagents",
    help=PROJECT_DESCRIPTION,
    no_args_is_help=True,
    add_completion=True,
)
runs_app = typer.Typer(help="Inspect and control durable research runs.")
db_app = typer.Typer(help="Maintain the local SQLite database.")
app.add_typer(runs_app, name="runs")
app.add_typer(db_app, name="db")

console = Console()
event_console = Console(stderr=True)
_ANALYSTS = ("market", "social", "news", "fundamentals")


class ExportFormat(StrEnum):
    __str__ = Enum.__str__

    PACKAGE = "package"
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
    try:
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
        )
    except ValidationError as exc:
        message = exc.errors(include_url=False)[0]["msg"]
        raise typer.BadParameter(message) from None
    application = _application()
    try:
        result = application.run(
            request,
            on_event=None if quiet else _print_event,
        )
    except UnsupportedInstrumentError as exc:
        raise typer.BadParameter(str(exc), param_hint="ticker") from None
    except InstrumentEligibilityUnavailableError as exc:
        event_console.print(
            "[red]Instrument eligibility is temporarily unavailable; "
            "please retry later.[/red]"
        )
        raise typer.Exit(code=1) from exc
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
    use_colors: Annotated[
        bool | None,
        typer.Option(
            "--use-colors/--no-use-colors",
            hidden=True,
        ),
    ] = None,
) -> None:
    """Serve the Web run center using the configured loopback or LAN policy."""
    settings = _settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=log_level,
        use_colors=use_colors,
        log_config=uvicorn_log_config(),
    )


@app.command()
def start(
    log_level: Annotated[str, typer.Option("--log-level")] = "info",
    color: Annotated[
        ColorMode,
        typer.Option(
            "--color",
            help="Color policy for merged Web and worker output.",
        ),
    ] = ColorMode.AUTO,
    log_dir: Annotated[
        Path | None,
        typer.Option(
            "--log-dir",
            file_okay=False,
            help="Optionally tee Web and worker output to rotating log files.",
        ),
    ] = None,
) -> None:
    """Start the local Web and worker together in the foreground."""
    code = LocalProcessSupervisor(
        _settings(),
        log_level=log_level,
        log_dir=log_dir,
        color_mode=color,
    ).run()
    if code:
        raise typer.Exit(code=code)


@app.command()
def worker(
    once: Annotated[
        bool,
        typer.Option("--once", help="Process at most one queue item."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level")] = "info",
    use_colors: Annotated[
        bool | None,
        typer.Option(
            "--use-colors/--no-use-colors",
            hidden=True,
        ),
    ] = None,
) -> None:
    """Run the single-concurrency analysis and outcome-settlement worker."""
    color_enabled = (
        sys.stderr.isatty() and "NO_COLOR" not in os.environ
        if use_colors is None
        else use_colors
    )
    if color_enabled:
        logging.basicConfig(
            level=getattr(logging, log_level.upper(), logging.INFO),
            format="%(message)s",
            handlers=[
                RichHandler(
                    console=Console(stderr=True, force_terminal=True),
                    show_path=False,
                    omit_repeated_times=False,
                    markup=False,
                    rich_tracebacks=True,
                )
            ],
            force=True,
        )
    else:
        logging.basicConfig(
            level=getattr(logging, log_level.upper(), logging.INFO),
            format="%(levelname)s [%(name)s] %(message)s",
            force=True,
        )
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
    page = _service().repository.list_runs(
        status=status,
        limit=limit,
        offset=offset,
    )
    items = page.items
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
    """Export a completed run as a research package, Markdown, or JSON."""
    if format is ExportFormat.PACKAGE and output is None:
        event_console.print(
            "[red]Package export requires --output because ZIP data "
            "cannot be written to the terminal.[/red]"
        )
        raise typer.Exit(code=2)
    try:
        _media_type, content = _service().export(run_id, format=format.value)
    except Exception as exc:
        _lifecycle_error(exc)
    if output is None:
        if not isinstance(content, str):
            raise typer.Exit(code=2)
        typer.echo(content)
        return
    _write_output(output, content, force=force)
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


def _write_output(path: Path, content: str | bytes, *, force: bool) -> None:
    destination = path.expanduser().resolve()
    if destination.exists() and not force:
        event_console.print(
            f"[red]Refusing to overwrite existing file: {destination}[/red]"
        )
        raise typer.Exit(code=1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        destination.write_bytes(content)
    else:
        destination.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    app()
