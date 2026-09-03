from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from typer.testing import CliRunner

import cli.main as cli
from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisResult,
    RunEvent,
    RunProfile,
    RunStatus,
)
from tradingagents.application.errors import (
    InstrumentEligibilityUnavailableError,
    UnsupportedInstrumentError,
)
from tradingagents.application.repository import RunRepository
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.persistence import upgrade_database

runner = CliRunner()


@pytest.fixture
def cli_settings(tmp_path: Path) -> AppSettings:
    return AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(tmp_path / "tradingagents.db"),
            "TRADINGAGENTS_CACHE_DIR": str(tmp_path / "cache"),
        },
        load_env_files=False,
    )


@pytest.fixture
def cli_service(cli_settings: AppSettings) -> AnalysisService:
    return AnalysisService(
        cli_settings,
        eligibility_resolver=lambda ticker: {
            "symbol": ticker,
            "quote_type": "EQUITY",
        },
    )


def test_root_is_noninteractive_and_exposes_the_new_command_tree() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "run",
        "start",
        "serve",
        "worker",
        "runs",
        "export",
        "db",
    ):
        assert command in result.output
    assert "memory" not in result.output
    assert "questionnaire" not in result.output.lower()
    run_help = runner.invoke(cli.app, ["run", "--help"])
    assert run_help.exit_code == 0
    assert "--provenance" not in run_help.output
    worker_help = runner.invoke(cli.app, ["worker", "--help"])
    assert worker_help.exit_code == 0
    assert "single-concurrency analysis worker" in worker_help.output
    assert "outcome-settlement" not in worker_help.output


def test_version_exits_without_loading_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_settings",
        lambda: pytest.fail("settings should not be loaded"),
    )

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip()


def test_run_builds_the_typed_request_and_prints_json(monkeypatch) -> None:
    captured = {}
    event = RunEvent(
        run_id="run-1",
        sequence=1,
        attempt=1,
        event_type="run.started",
        created_at=datetime.now(UTC),
    )
    result = AnalysisResult(
        run_id="run-1",
        status=RunStatus.SUCCEEDED,
        instrument="7203.T",
        reports={},
        decision=research_decision(
            confidence="medium",
            thesis="Balanced evidence.",
        ),
    )

    class FakeApplication:
        def run(self, request, *, on_event):
            captured["request"] = request
            captured["on_event"] = on_event
            if on_event:
                on_event(event)
            return result

    monkeypatch.setattr(cli, "_application", FakeApplication)
    invocation = runner.invoke(
        cli.app,
        [
            "run",
            "7203.t",
            "--date",
            "2026-07-24",
            "--profile",
            "deep",
            "--analysts",
            "news,market",
            "--provider",
            "openai",
            "--quick-model",
            "quick",
            "--deep-model",
            "deep",
            "--quick-reasoning",
            "low",
            "--deep-reasoning",
            "high",
            "--output-language",
            "ja",
            "--quiet",
            "--json",
        ],
    )

    assert invocation.exit_code == 0
    payload = json.loads(invocation.stdout)
    assert payload["run_id"] == "run-1"
    request = captured["request"]
    assert request.ticker == "7203.T"
    assert request.profile is RunProfile.DEEP
    assert request.analysts == ("market", "news")
    assert request.output_language == "ja"
    assert request.quick_reasoning_effort == "low"
    assert request.deep_reasoning_effort == "high"
    assert captured["on_event"] is None


def test_run_prints_confidence_level_in_non_json_success_summary(monkeypatch) -> None:
    class FakeApplication:
        def run(self, request, *, on_event):
            return AnalysisResult(
                run_id="run-readable-summary",
                status=RunStatus.SUCCEEDED,
                instrument=request.ticker,
                reports={},
                decision=research_decision(confidence="medium"),
            )

    monkeypatch.setattr(cli, "_application", FakeApplication)

    result = runner.invoke(
        cli.app,
        ["run", "AAPL", "--date", "2026-07-24", "--quiet"],
    )

    assert result.exit_code == 0
    assert "confidence medium" in result.output


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (UnsupportedInstrumentError("SPY", "etf"), 2),
        (InstrumentEligibilityUnavailableError("NVDA"), 1),
    ],
)
def test_run_distinguishes_usage_and_operational_admission_errors(
    monkeypatch,
    error,
    exit_code,
) -> None:
    class FakeApplication:
        def run(self, _request, *, on_event):
            raise error

    monkeypatch.setattr(cli, "_application", lambda: FakeApplication())
    result = runner.invoke(
        cli.app,
        ["run", "SPY", "--date", "2026-07-24", "--quiet"],
    )

    assert result.exit_code == exit_code
    if isinstance(error, UnsupportedInstrumentError):
        assert "SPY" in result.output
    else:
        assert "temporarily unavailable" in result.output


def test_run_defaults_to_the_instrument_market_date(monkeypatch) -> None:
    captured = {}

    class FakeApplication:
        def run(self, request, *, on_event):
            captured["request"] = request
            return AnalysisResult(
                run_id="run-2",
                status=RunStatus.SUCCEEDED,
                instrument=request.ticker,
                reports={},
                decision=None,
            )

    monkeypatch.setattr(cli, "_application", FakeApplication)
    monkeypatch.setattr(
        cli,
        "market_today",
        lambda ticker: (
            captured.setdefault("market_ticker", ticker),
            __import__("datetime").date(2026, 7, 27),
        )[1],
    )

    result = runner.invoke(cli.app, ["run", "AAPL", "--quiet"])

    assert result.exit_code == 0
    assert captured["market_ticker"] == "AAPL"
    assert captured["request"].analysis_date.isoformat() == "2026-07-27"
    assert captured["request"].output_language is None


def test_run_preserves_custom_output_language(monkeypatch) -> None:
    captured = {}
    custom_language = "Use concise Simplified Chinese; retain source names."

    class FakeApplication:
        def run(self, request, *, on_event):
            captured["request"] = request
            return AnalysisResult(
                run_id="run-language",
                status=RunStatus.SUCCEEDED,
                instrument=request.ticker,
                reports={},
                decision=None,
            )

    monkeypatch.setattr(cli, "_application", FakeApplication)

    result = runner.invoke(
        cli.app,
        [
            "run",
            "AAPL",
            "--date",
            "2026-07-24",
            "--output-language",
            custom_language,
            "--quiet",
        ],
    )

    assert result.exit_code == 0
    assert captured["request"].output_language == custom_language


def test_run_prints_persisted_progress_events(monkeypatch) -> None:
    event = RunEvent(
        run_id="run-progress",
        sequence=4,
        attempt=1,
        event_type="node.completed",
        node="market",
        created_at=datetime.now(UTC),
    )

    class FakeApplication:
        def run(self, request, *, on_event):
            assert on_event is not None
            on_event(event)
            return AnalysisResult(
                run_id=event.run_id,
                status=RunStatus.SUCCEEDED,
                instrument=request.ticker,
                reports={},
                decision=None,
            )

    monkeypatch.setattr(cli, "_application", FakeApplication)

    result = runner.invoke(
        cli.app,
        ["run", "AAPL", "--date", "2026-07-24"],
    )

    assert result.exit_code == 0
    assert "#4" in result.output
    assert "node.completed · market" in result.output


def test_run_failure_redacts_exception_details(monkeypatch) -> None:
    class FakeApplication:
        def run(self, request, *, on_event):
            raise RuntimeError("provider rejected sk-private-token")

    monkeypatch.setattr(cli, "_application", FakeApplication)

    result = runner.invoke(
        cli.app,
        ["run", "AAPL", "--date", "2026-07-24"],
    )

    assert result.exit_code == 1
    assert "Analysis failed (RuntimeError)" in result.output
    assert "sk-private-token" not in result.output


def test_run_reports_market_date_resolution_as_a_usage_error(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "market_today",
        lambda ticker: (_ for _ in ()).throw(ValueError(f"unsupported market symbol: {ticker}")),
    )

    result = runner.invoke(cli.app, ["run", "INVALID@SYMBOL"])

    assert result.exit_code == 2
    assert "unsupported market symbol" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["run", "AAPL", "--date", "not-a-date"], "expected YYYY-MM-DD"),
        (["run", "AAPL", "--analysts", "market,unknown"], "unknown analyst"),
        (["run", "AAPL", "--analysts", ""], "at least one analyst"),
    ],
)
def test_run_rejects_invalid_noninteractive_options(
    arguments: list[str],
    message: str,
) -> None:
    result = runner.invoke(cli.app, arguments)

    assert result.exit_code == 2
    assert message in result.output


def test_serve_uses_the_validated_application_binding(
    monkeypatch,
    cli_settings: AppSettings,
) -> None:
    calls = {}
    monkeypatch.setattr(cli, "_settings", lambda: cli_settings)
    monkeypatch.setattr(cli, "create_app", lambda settings: ("app", settings))
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: calls.update({"app": app, **kwargs}),
    )

    result = runner.invoke(cli.app, ["serve", "--log-level", "warning"])

    assert result.exit_code == 0
    assert calls["app"] == ("app", cli_settings)
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8000
    assert calls["log_level"] == "warning"
    assert calls["use_colors"] is None
    assert calls["log_config"]["handlers"]["access"]["filters"] == ["successful_static_assets"]


def test_start_supervises_web_and_worker(
    monkeypatch,
    cli_settings: AppSettings,
    tmp_path: Path,
) -> None:
    calls = {}

    class FakeSupervisor:
        def __init__(self, settings, **kwargs):
            calls.update({"settings": settings, **kwargs})

        def run(self):
            return 0

    monkeypatch.setattr(cli, "_settings", lambda: cli_settings)
    monkeypatch.setattr(cli, "LocalProcessSupervisor", FakeSupervisor)
    log_dir = tmp_path / "logs"

    result = runner.invoke(
        cli.app,
        [
            "start",
            "--log-level",
            "warning",
            "--log-dir",
            str(log_dir),
        ],
    )

    assert result.exit_code == 0
    assert calls == {
        "settings": cli_settings,
        "log_level": "warning",
        "log_dir": log_dir,
        "color_mode": cli.ColorMode.AUTO,
    }


def test_worker_once_processes_at_most_one_item(
    monkeypatch,
    cli_settings: AppSettings,
) -> None:
    calls = []

    class FakeWorker:
        def __init__(self, settings):
            assert settings is cli_settings

        def run_once(self):
            calls.append("once")
            return False

    monkeypatch.setattr(cli, "_settings", lambda: cli_settings)
    monkeypatch.setattr(cli, "AnalysisWorker", FakeWorker)

    result = runner.invoke(cli.app, ["worker", "--once"])

    assert result.exit_code == 0
    assert calls == ["once"]
    assert "Queue is empty" in result.output


def test_worker_colored_logging_repeats_timestamps(
    monkeypatch,
    cli_settings: AppSettings,
) -> None:
    configured = {}

    class FakeWorker:
        def __init__(self, settings):
            assert settings is cli_settings

        def run_once(self):
            return False

    monkeypatch.setattr(cli, "_settings", lambda: cli_settings)
    monkeypatch.setattr(cli, "AnalysisWorker", FakeWorker)
    monkeypatch.setattr(
        cli.logging,
        "basicConfig",
        lambda **kwargs: configured.update(kwargs),
    )

    result = runner.invoke(
        cli.app,
        ["worker", "--once", "--use-colors"],
    )

    assert result.exit_code == 0
    handler = configured["handlers"][0]
    assert handler._log_render.omit_repeated_times is False


def test_runs_list_show_and_cancel(
    monkeypatch,
    cli_service: AnalysisService,
) -> None:
    queued = cli_service.enqueue(cli.AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))
    monkeypatch.setattr(cli, "_service", lambda: cli_service)

    listed = runner.invoke(cli.app, ["runs", "list", "--json"])
    shown = runner.invoke(cli.app, ["runs", "show", queued.id])
    cancelled = runner.invoke(cli.app, ["runs", "cancel", queued.id])

    assert listed.exit_code == shown.exit_code == cancelled.exit_code == 0
    assert json.loads(listed.stdout)[0]["id"] == queued.id
    assert json.loads(shown.stdout)["result"] is None
    assert json.loads(cancelled.stdout)["status"] == "cancelled"
    assert runner.invoke(cli.app, ["runs", "rerun", queued.id]).exit_code != 0


def test_runs_retry_creates_a_new_attempt(
    monkeypatch,
    cli_service: AnalysisService,
) -> None:
    queued = cli_service.enqueue(cli.AnalysisRequest(ticker="AAPL", analysis_date="2026-07-24"))
    claimed = cli_service.repository.claim_run(queued.id, "test-worker", 300)
    assert claimed.status is RunStatus.RUNNING
    cli_service.repository.fail(queued.id, RuntimeError("provider failed"))
    monkeypatch.setattr(cli, "_service", lambda: cli_service)

    retried = runner.invoke(cli.app, ["runs", "retry", queued.id])

    assert retried.exit_code == 0
    payload = json.loads(retried.stdout)
    assert payload["id"] == queued.id
    assert payload["status"] == "queued"
    assert payload["attempt"] == 2


def test_export_writes_to_stdout_when_output_is_omitted(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_service",
        lambda: SimpleNamespace(
            export=lambda run_id, format: (
                "application/json",
                json.dumps({"run_id": run_id, "format": format}),
            )
        ),
    )

    result = runner.invoke(
        cli.app,
        ["export", "run-json", "--format", "json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "run_id": "run-json",
        "format": "json",
    }


def test_export_refuses_overwrite_without_force(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = SimpleNamespace(
        export=lambda run_id, format: (
            "text/markdown",
            f"# {run_id} ({format})",
        )
    )
    monkeypatch.setattr(cli, "_service", lambda: service)
    destination = tmp_path / "report.md"

    created = runner.invoke(
        cli.app,
        ["export", "run-1", "--output", str(destination)],
    )
    refused = runner.invoke(
        cli.app,
        ["export", "run-1", "--output", str(destination)],
    )
    overwritten = runner.invoke(
        cli.app,
        ["export", "run-2", "--output", str(destination), "--force"],
    )

    assert created.exit_code == overwritten.exit_code == 0
    assert refused.exit_code == 1
    assert "Refusing to overwrite" in refused.output
    assert destination.read_text(encoding="utf-8") == "# run-2 (markdown)"


def test_package_export_requires_output_and_writes_binary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = b"PK\x03\x04fixture-package"
    monkeypatch.setattr(
        cli,
        "_service",
        lambda: SimpleNamespace(
            export=lambda run_id, format: (
                "application/zip",
                payload,
            )
        ),
    )

    refused = runner.invoke(cli.app, ["export", "run-1", "--format", "package"])
    destination = tmp_path / "research.zip"
    written = runner.invoke(
        cli.app,
        [
            "export",
            "run-1",
            "--format",
            "package",
            "--output",
            str(destination),
        ],
    )

    assert refused.exit_code == 2
    assert "requires --output" in refused.output
    assert written.exit_code == 0
    assert destination.read_bytes() == payload


def test_db_backup_preserves_a_pre_migration_database_and_legacy_reviews(
    monkeypatch,
    cli_settings: AppSettings,
    tmp_path: Path,
) -> None:
    upgrade_database(cli_settings)
    repository = RunRepository(cli_settings)
    request = cli.AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
    )
    run, _ = repository.create_run(request, {"fixture": True})
    repository.engine.dispose()
    migration_root = resources.files("tradingagents.persistence").joinpath("alembic")
    with resources.as_file(migration_root) as script_location:
        config = Config()
        config.set_main_option("script_location", str(script_location))
        config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{cli_settings.database_path}")
        config.attributes["busy_timeout_ms"] = cli_settings.busy_timeout_ms
        alembic_command.downgrade(config, "0004_instrument_local_name")
    repository = RunRepository(cli_settings)
    with repository.engine.begin() as connection:
        decision_id = connection.exec_driver_sql(
            "INSERT INTO decisions "
            "(run_id, ticker, market, asset_type, analysis_date, rating, "
            "confidence, decision_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                run.id,
                request.ticker,
                "US",
                "stock",
                request.analysis_date.isoformat(),
                "buy",
                0.5,
                "{}",
                "2026-08-20 00:00:00",
            ),
        ).scalar_one()
        connection.exec_driver_sql(
            "INSERT INTO outcomes "
            "(decision_id, status, benchmark, holding_intervals, next_check_at) "
            "VALUES (?, 'pending', 'SPY', 5, '2026-07-24 00:00:00')",
            (decision_id,),
        )
        outcome_id = connection.exec_driver_sql(
            "SELECT id FROM outcomes WHERE decision_id = ?",
            (decision_id,),
        ).scalar_one()
        connection.exec_driver_sql(
            "INSERT INTO reflections (outcome_id, text, created_at) "
            "VALUES (?, ?, '2026-08-20 00:00:00')",
            (outcome_id, "Legacy reflection."),
        )
    repository.engine.dispose()

    destination = tmp_path / "backup" / "pre-migration.db"
    monkeypatch.setattr(cli, "_settings", lambda: cli_settings)
    monkeypatch.setattr(
        cli,
        "_service",
        lambda: pytest.fail("backup must not construct AnalysisService"),
    )

    result = runner.invoke(cli.app, ["db", "backup", str(destination)])

    assert result.exit_code == 0
    with sqlite3.connect(cli_settings.database_path) as source:
        assert source.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0004_instrument_local_name",
        )
        assert source.execute("SELECT count(*) FROM outcomes").fetchone() == (1,)
        assert source.execute("SELECT count(*) FROM reflections").fetchone() == (1,)
    with sqlite3.connect(destination) as backup:
        assert backup.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0004_instrument_local_name",
        )
        assert backup.execute("SELECT count(*) FROM outcomes").fetchone() == (1,)
        assert backup.execute("SELECT count(*) FROM reflections").fetchone() == (1,)

    upgraded_settings = cli_settings.model_copy(update={"database_path": destination})
    upgrade_database(upgraded_settings)
    upgraded_repository = RunRepository(upgraded_settings)
    try:
        assert upgraded_repository.get_run(run.id).request.ticker == "NVDA"
        with sqlite3.connect(destination) as upgraded:
            assert upgraded.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0010_decision_confidence_levels",
            )
            assert (
                upgraded.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name IN ('outcomes', 'reflections')"
                ).fetchall()
                == []
            )
    finally:
        upgraded_repository.engine.dispose()


def test_database_backup_is_consistent_and_refuses_overwrite(
    monkeypatch,
    cli_service: AnalysisService,
    tmp_path: Path,
) -> None:
    cli_service.enqueue(cli.AnalysisRequest(ticker="MSFT", analysis_date="2026-07-24"))
    monkeypatch.setattr(cli, "_settings", lambda: cli_service.settings)
    destination = tmp_path / "backup.db"

    created = runner.invoke(cli.app, ["db", "backup", str(destination)])
    refused = runner.invoke(cli.app, ["db", "backup", str(destination)])

    assert created.exit_code == 0
    assert destination.is_file()
    assert destination.stat().st_size > 0
    assert refused.exit_code == 1
    assert "Refusing to overwrite" in refused.output
