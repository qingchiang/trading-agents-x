from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import cli.main as cli
from tradingagents.application.contracts import (
    AnalysisResult,
    ResearchDecision,
    ResearchRating,
    RunEvent,
    RunProfile,
    RunStatus,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings

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
    return AnalysisService(cli_settings)


def test_root_is_noninteractive_and_exposes_the_new_command_tree() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "serve", "worker", "runs", "memory", "export", "db"):
        assert command in result.output
    assert "questionnaire" not in result.output.lower()


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
        created_at=datetime.now(timezone.utc),
    )
    result = AnalysisResult(
        run_id="run-1",
        status=RunStatus.SUCCEEDED,
        instrument="7203.T",
        reports={},
        decision=ResearchDecision(
            rating=ResearchRating.HOLD,
            confidence=0.7,
            thesis="Balanced evidence.",
            time_horizon="6-12 months",
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
            "--no-provenance",
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
    assert request.provenance is False
    assert request.quick_reasoning_effort == "low"
    assert request.deep_reasoning_effort == "high"
    assert captured["on_event"] is None


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
    assert captured["request"].provenance is None


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
        created_at=datetime.now(timezone.utc),
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
        lambda ticker: (_ for _ in ()).throw(
            ValueError(f"unsupported market symbol: {ticker}")
        ),
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


def test_runs_list_show_cancel_and_rerun(
    monkeypatch,
    cli_service: AnalysisService,
) -> None:
    queued = cli_service.enqueue(
        cli.AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )
    monkeypatch.setattr(cli, "_service", lambda: cli_service)

    listed = runner.invoke(cli.app, ["runs", "list", "--json"])
    shown = runner.invoke(cli.app, ["runs", "show", queued.id])
    cancelled = runner.invoke(cli.app, ["runs", "cancel", queued.id])
    rerun = runner.invoke(cli.app, ["runs", "rerun", queued.id])

    assert listed.exit_code == shown.exit_code == cancelled.exit_code == rerun.exit_code == 0
    assert json.loads(listed.stdout)[0]["id"] == queued.id
    assert json.loads(shown.stdout)["result"] is None
    assert json.loads(cancelled.stdout)["status"] == "cancelled"
    rerun_payload = json.loads(rerun.stdout)
    assert rerun_payload["parent_run_id"] == queued.id
    assert rerun_payload["id"] != queued.id


def test_runs_retry_creates_a_new_attempt(
    monkeypatch,
    cli_service: AnalysisService,
) -> None:
    queued = cli_service.enqueue(
        cli.AnalysisRequest(ticker="AAPL", analysis_date="2026-07-24")
    )
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


def test_memory_import_defaults_to_dry_run_and_can_apply(
    monkeypatch,
    cli_service: AnalysisService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "trading_memory.md"
    original = (
        "[2026-01-10 | NVDA | Buy | +4.2% | +2.1% | 5d]\n\n"
        "META: asset_type=stock | market=America/New_York\n\n"
        "DECISION:\nBuy because demand accelerated.\n"
        "REFLECTION:\n"
        "[2026-01-12 → 2026-01-20 | 5d]\nThe evidence was useful."
    )
    source.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "_service", lambda: cli_service)

    dry_run = runner.invoke(cli.app, ["memory", "import", str(source)])
    applied = runner.invoke(
        cli.app,
        ["memory", "import", str(source), "--apply"],
    )

    assert dry_run.exit_code == applied.exit_code == 0
    assert json.loads(dry_run.stdout)["dry_run"] is True
    imported = json.loads(applied.stdout)
    assert imported["imported"] == 1
    assert Path(imported["backup"]).read_text(encoding="utf-8") == original
    assert source.read_text(encoding="utf-8") == original


def test_memory_import_reports_malformed_blocks_without_mutating_source(
    monkeypatch,
    cli_service: AnalysisService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "malformed.md"
    original = "not a legacy memory block"
    source.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "_service", lambda: cli_service)

    result = runner.invoke(cli.app, ["memory", "import", str(source)])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["malformed"] == 1
    assert source.read_text(encoding="utf-8") == original


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


def test_database_backup_is_consistent_and_refuses_overwrite(
    monkeypatch,
    cli_service: AnalysisService,
    tmp_path: Path,
) -> None:
    cli_service.enqueue(
        cli.AnalysisRequest(ticker="MSFT", analysis_date="2026-07-24")
    )
    monkeypatch.setattr(cli, "_service", lambda: cli_service)
    destination = tmp_path / "backup.db"

    created = runner.invoke(cli.app, ["db", "backup", str(destination)])
    refused = runner.invoke(cli.app, ["db", "backup", str(destination)])

    assert created.exit_code == 0
    assert destination.is_file()
    assert destination.stat().st_size > 0
    assert refused.exit_code == 1
    assert "Refusing to overwrite" in refused.output
