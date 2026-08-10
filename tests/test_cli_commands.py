from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import cli.main as cli
from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisResult,
    RunEvent,
    RunProfile,
    RunStatus,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings

runner = CliRunner()


def _git_checkout_for_live_validation(tmp_path: Path) -> tuple[Path, Path, str]:
    checkout = tmp_path / "checkout"
    (checkout / "cli").mkdir(parents=True)
    (checkout / "cli/main.py").write_text("# tracked source\n", encoding="utf-8")
    (checkout / ".gitignore").write_text(".env\n*.db\ntmp/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    cases = checkout / "tmp/incremental-research/reviewed-live-cases.json"
    cases.parent.mkdir(parents=True)
    cases.write_text("[]", encoding="utf-8")
    return checkout, cases, commit


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
    for command in (
        "run",
        "start",
        "serve",
        "worker",
        "runs",
        "export",
        "db",
        "research",
    ):
        assert command in result.output
    assert "memory" not in result.output
    assert "questionnaire" not in result.output.lower()
    run_help = runner.invoke(cli.app, ["run", "--help"])
    assert run_help.exit_code == 0
    assert "--provenance" not in run_help.output


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
        decision=research_decision(
            confidence=0.7,
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


def test_run_rejects_crypto_instruments_before_starting_analysis(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_application",
        lambda: pytest.fail("application should not be created"),
    )

    result = runner.invoke(
        cli.app,
        ["run", "BTC-USD", "--date", "2026-07-24"],
    )

    assert result.exit_code == 2
    assert "Crypto instruments are not supported" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("ticker", ["EURUSD", "GC=F", "^GSPC"])
def test_run_rejects_non_equity_instruments_before_starting_analysis(
    monkeypatch,
    ticker: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "_application",
        lambda: pytest.fail("application should not be created"),
    )

    result = runner.invoke(
        cli.app,
        ["run", ticker, "--date", "2026-07-24"],
    )

    assert result.exit_code == 2
    assert "Only listed equity instruments are supported" in result.output
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
    assert calls["log_config"]["handlers"]["access"]["filters"] == [
        "successful_static_assets"
    ]


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
    queued = cli_service.enqueue(
        cli.AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )
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


def test_live_thesis_validation_cli_requires_explicit_in_place_flag_and_reports_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(cli, "load_reviewed_scenarios", lambda path: (str(path),))
    monkeypatch.setattr(cli, "_service", lambda: "service")
    monkeypatch.setattr(
        cli,
        "_source_checkout",
        lambda: (tmp_path / "checkout", "a" * 40),
    )

    def validate(service, scenarios, **kwargs):
        captured.update(service=service, scenarios=scenarios, **kwargs)
        return SimpleNamespace(
            manifest_directory=tmp_path / "manifest" / "session",
            passed=True,
            entries=(SimpleNamespace(scenario="quiet_interval", validation_verdict="passed"),),
        )

    monkeypatch.setattr(cli, "validate_live_thesis", validate)
    refused = runner.invoke(
        cli.app,
        [
            "research",
            "validate-live-thesis",
            str(cases),
            "--backup",
            str(tmp_path / "backup.db"),
        ],
    )
    completed = runner.invoke(
        cli.app,
        [
            "research",
            "validate-live-thesis",
            str(cases),
            "--backup",
            str(tmp_path / "backup.db"),
            "--in-place-database",
        ],
    )

    assert refused.exit_code == 2
    assert completed.exit_code == 0
    assert captured["service"] == "service"
    assert captured["in_place_database"] is True
    assert captured["git_commit"] == "a" * 40
    assert captured["manifest_root"] == (
        tmp_path / "checkout" / "tmp/incremental-research/live-validation"
    )
    assert "manifest/session" in completed.output
    assert "quiet_interval: passed" in completed.output

    monkeypatch.setattr(
        cli,
        "validate_live_thesis",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_directory=tmp_path / "manifest" / "mismatch",
            passed=False,
            entries=(
                SimpleNamespace(
                    scenario="quiet_interval",
                    validation_verdict="expectation_mismatch",
                ),
            ),
        ),
    )
    mismatch = runner.invoke(
        cli.app,
        [
            "research",
            "validate-live-thesis",
            str(cases),
            "--backup",
            str(tmp_path / "backup-2.db"),
            "--in-place-database",
        ],
    )

    assert mismatch.exit_code == 1
    assert "quiet_interval: expectation_mismatch" in mismatch.output


def test_live_thesis_validation_cli_accepts_clean_checkout_with_ignored_runtime_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkout, cases, commit = _git_checkout_for_live_validation(tmp_path)
    (checkout / ".env").write_text("IGNORED_SECRET=fixture\n", encoding="utf-8")
    (checkout / "configured.db").write_bytes(b"ignored database")
    (checkout / "backup.db").write_bytes(b"ignored backup")
    manifest = checkout / "tmp/incremental-research/live-validation/old.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(cli, "__file__", str(checkout / "cli/main.py"))
    monkeypatch.setattr(cli, "load_reviewed_scenarios", lambda _path: ())
    monkeypatch.setattr(cli, "_service", lambda: "service")

    def validate(_service, _scenarios, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            manifest_directory=manifest.parent / "new-session",
            passed=True,
            entries=(),
        )

    monkeypatch.setattr(cli, "validate_live_thesis", validate)

    result = runner.invoke(
        cli.app,
        [
            "research",
            "validate-live-thesis",
            str(cases),
            "--backup",
            str(checkout / "new-backup.db"),
            "--in-place-database",
        ],
    )

    assert result.exit_code == 0
    assert captured["git_commit"] == commit
    assert captured["manifest_root"] == manifest.parent


@pytest.mark.parametrize("dirty_kind", ["staged", "modified", "untracked"])
def test_live_thesis_validation_cli_refuses_dirty_source_before_application_work(
    monkeypatch,
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    checkout, cases, _commit = _git_checkout_for_live_validation(tmp_path)
    tracked = checkout / "cli/main.py"
    if dirty_kind == "staged":
        tracked.write_text("# staged source\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(checkout), "add", "cli/main.py"], check=True)
    elif dirty_kind == "modified":
        tracked.write_text("# modified source\n", encoding="utf-8")
    else:
        (checkout / "ordinary-untracked.txt").write_text("source", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(tracked))
    monkeypatch.setattr(
        cli,
        "load_reviewed_scenarios",
        lambda _path: pytest.fail("cases must not load for a dirty checkout"),
    )
    monkeypatch.setattr(
        cli,
        "_service",
        lambda: pytest.fail("service must not load for a dirty checkout"),
    )

    result = runner.invoke(
        cli.app,
        [
            "research",
            "validate-live-thesis",
            str(cases),
            "--backup",
            str(checkout / "new-backup.db"),
            "--in-place-database",
        ],
    )

    assert result.exit_code == 1
    assert "clean source checkout" in result.output


def test_live_thesis_validation_cli_refuses_head_change_during_source_check(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkout, cases, _commit = _git_checkout_for_live_validation(tmp_path)
    tracked = checkout / "cli/main.py"
    original_run = subprocess.run

    def change_head_after_status(*args, **kwargs):
        completed = original_run(*args, **kwargs)
        command = args[0]
        if "status" in command:
            original_run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "changed head",
                ],
                check=True,
            )
        return completed

    monkeypatch.setattr(cli, "__file__", str(tracked))
    monkeypatch.setattr(cli.subprocess, "run", change_head_after_status)
    monkeypatch.setattr(
        cli,
        "load_reviewed_scenarios",
        lambda _path: pytest.fail("cases must not load after HEAD changes"),
    )

    result = runner.invoke(
        cli.app,
        [
            "research",
            "validate-live-thesis",
            str(cases),
            "--backup",
            str(checkout / "new-backup.db"),
            "--in-place-database",
        ],
    )

    assert result.exit_code == 1
    assert "Git commit changed" in result.output
    assert "verification" in result.output
