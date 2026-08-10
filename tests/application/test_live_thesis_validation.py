from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tradingagents.application.contracts import RunMetrics, RunStatus
from tradingagents.application.live_thesis_validation import (
    LiveThesisValidationError,
    load_reviewed_scenarios,
    validate_live_thesis,
)
from tradingagents.application.metrics import merge_run_metrics
from tradingagents.application.research import ResearchChangeConclusion


class _MustNotRunService:
    settings = SimpleNamespace(research_update_mode="shadow")

    def backup_database(self, _destination: Path) -> Path:
        raise AssertionError("backup must not run before every opt-in is present")


def _loaded_scenarios(tmp_path: Path):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(_reviewed_payload()), encoding="utf-8")
    return load_reviewed_scenarios(cases_path)


@pytest.mark.parametrize(
    ("environ", "in_place_database", "missing"),
    [
        ({"RUN_LIVE_LLM_TESTS": "1"}, True, "RUN_LIVE_DATA_TESTS"),
        ({"RUN_LIVE_DATA_TESTS": "1"}, True, "RUN_LIVE_LLM_TESTS"),
        (
            {"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
            False,
            "in-place database",
        ),
    ],
)
def test_live_thesis_validation_refuses_before_backup_without_every_opt_in(
    tmp_path: Path,
    environ: dict[str, str],
    in_place_database: bool,
    missing: str,
) -> None:
    with pytest.raises(LiveThesisValidationError, match=missing):
        validate_live_thesis(
            _MustNotRunService(),
            (),
            backup_destination=tmp_path / "backup.db",
            manifest_root=tmp_path / "manifest",
            git_commit="a" * 40,
            environ=environ,
            in_place_database=in_place_database,
            verify_source_checkout=lambda: None,
            validate_market_readiness=lambda _request: None,
        )


def _reviewed_payload() -> list[dict[str, str]]:
    return [
        {
            "scenario": "quiet_interval",
            "chain_id": "chain-quiet",
            "analysis_date": "2026-08-10",
            "expected_bounded_result": "no_material_change",
            "expected_full_change_conclusion": "indeterminate",
        },
        {
            "scenario": "material_event",
            "chain_id": "chain-material",
            "analysis_date": "2026-08-10",
            "expected_bounded_result": "semantic_weakening",
            "expected_full_change_conclusion": "material_change",
        },
        {
            "scenario": "source_integrity",
            "chain_id": "chain-integrity",
            "analysis_date": "2026-08-10",
            "expected_bounded_result": "source_correction",
            "expected_full_change_conclusion": "no_material_change",
        },
        {
            "scenario": "missing_coverage",
            "chain_id": "chain-coverage",
            "analysis_date": "2026-08-10",
            "expected_bounded_result": "coverage_incomplete",
            "expected_full_change_conclusion": "indeterminate",
        },
        {
            "scenario": "threshold_crossing",
            "chain_id": "chain-threshold",
            "analysis_date": "2026-08-10",
            "expected_bounded_result": "threshold_crossing",
            "expected_full_change_conclusion": "material_change",
        },
    ]


def test_reviewed_scenarios_require_exact_set_distinct_chains_and_strict_fields(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(_reviewed_payload()), encoding="utf-8")

    scenarios = load_reviewed_scenarios(cases_path)

    assert len(scenarios) == 5
    assert len({scenario.chain_id for scenario in scenarios}) == 5
    payload = _reviewed_payload()
    payload[1]["chain_id"] = payload[0]["chain_id"]
    cases_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LiveThesisValidationError, match="distinct Research Chain"):
        load_reviewed_scenarios(cases_path)


def test_reviewed_scenarios_reject_unreviewed_result_for_scenario(
    tmp_path: Path,
) -> None:
    payload = _reviewed_payload()
    payload[0]["expected_bounded_result"] = "threshold_crossing"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LiveThesisValidationError, match="quiet_interval"):
        load_reviewed_scenarios(cases_path)


def test_reviewed_scenarios_require_all_full_change_conclusions(tmp_path: Path) -> None:
    payload = _reviewed_payload()
    for item in payload:
        item["expected_full_change_conclusion"] = "material_change"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LiveThesisValidationError, match="every Full Change Conclusion"):
        load_reviewed_scenarios(cases_path)


def test_market_readiness_failure_prevents_backup_and_authoritative_execution(
    tmp_path: Path,
) -> None:
    scenarios = _loaded_scenarios(tmp_path)
    chains = {
        scenario.chain_id: SimpleNamespace(
            id=scenario.chain_id,
            instrument="6501.T",
            next_update_policy="incremental_allowed",
            current_revision=SimpleNamespace(
                id=f"baseline-{scenario.scenario}",
                cutoff=date(2026, 8, 9),
            ),
        )
        for scenario in scenarios
    }

    class ReadinessFailureService:
        settings = SimpleNamespace(research_update_mode="shadow")

        def get_research_chain(self, chain_id):
            return chains[chain_id]

        def backup_database(self, _destination: Path) -> Path:
            raise AssertionError("backup must not run before market data is ready")

        def run_chain_update(self, *_args, **_kwargs):
            raise AssertionError("execution must not run before market data is ready")

    checked = []

    def validate_market_readiness(request):
        checked.append(request)
        raise RuntimeError("J-Quants daily bar is not ready")

    with pytest.raises(LiveThesisValidationError, match="market data is not ready"):
        validate_live_thesis(
            ReadinessFailureService(),
            scenarios,
            backup_destination=tmp_path / "backup.db",
            manifest_root=tmp_path / "manifest",
            git_commit="a" * 40,
            environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
            in_place_database=True,
            verify_source_checkout=lambda: None,
            validate_market_readiness=validate_market_readiness,
        )

    assert len(checked) == 1
    assert checked[0].ticker == "6501.T"
    assert not (tmp_path / "backup.db").exists()
    assert not (tmp_path / "manifest").exists()


def test_backup_failure_prevents_authoritative_execution_and_manifest(
    tmp_path: Path,
) -> None:
    chains = {
        item["chain_id"]: SimpleNamespace(
            id=item["chain_id"],
            instrument="6501.T",
            next_update_policy="incremental_allowed",
            current_revision=SimpleNamespace(
                id=f"revision-{item['scenario']}",
                cutoff=date(2026, 8, 9),
            ),
        )
        for item in _reviewed_payload()
    }

    class BackupFailureService:
        settings = SimpleNamespace(
            research_update_mode="shadow",
            database_path=tmp_path / "tradingagents.db",
        )
        repository = SimpleNamespace(get_research_chain=chains.__getitem__)

        def get_research_chain(self, chain_id):
            return chains[chain_id]

        def backup_database(self, _destination: Path) -> Path:
            raise OSError("fixture backup failure")

        def enqueue_chain_update(self, *_args, **_kwargs):
            raise AssertionError("no execution may be queued after backup failure")

    manifest_root = tmp_path / "manifest"
    with pytest.raises(LiveThesisValidationError, match="backup failed"):
        validate_live_thesis(
            BackupFailureService(),
            _loaded_scenarios(tmp_path),
            backup_destination=tmp_path / "backup.db",
            manifest_root=manifest_root,
            git_commit="a" * 40,
            environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
            in_place_database=True,
            verify_source_checkout=lambda: None,
            validate_market_readiness=lambda _request: None,
        )

    assert not manifest_root.exists()


def test_source_policy_rejection_prevents_backup_and_execution(tmp_path: Path) -> None:
    scenarios = _loaded_scenarios(tmp_path)
    chains = {
        scenario.chain_id: SimpleNamespace(
            id=scenario.chain_id,
            instrument="6501.T",
            next_update_policy=(
                "full_required"
                if scenario.scenario == "source_integrity"
                else "incremental_allowed"
            ),
            current_revision=SimpleNamespace(
                id=f"baseline-{scenario.scenario}",
                cutoff=date(2026, 8, 9),
            ),
        )
        for scenario in scenarios
    }

    class SourcePolicyFailureService:
        settings = SimpleNamespace(research_update_mode="shadow")

        def get_research_chain(self, chain_id):
            return chains[chain_id]

        def backup_database(self, _destination: Path) -> Path:
            raise AssertionError("backup must not run for a source-ineligible head")

        def run_chain_update(self, *_args, **_kwargs):
            raise AssertionError("execution must not run for a source-ineligible head")

    with pytest.raises(LiveThesisValidationError, match="not an Eligible Baseline"):
        validate_live_thesis(
            SourcePolicyFailureService(),
            scenarios,
            backup_destination=tmp_path / "backup.db",
            manifest_root=tmp_path / "manifest",
            git_commit="a" * 40,
            environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
            in_place_database=True,
            verify_source_checkout=lambda: None,
            validate_market_readiness=lambda _request: None,
        )

    assert not (tmp_path / "backup.db").exists()
    assert not (tmp_path / "manifest").exists()


def test_source_is_reverified_after_backup_before_each_execution(tmp_path: Path) -> None:
    scenarios = _loaded_scenarios(tmp_path)
    chains = {
        scenario.chain_id: SimpleNamespace(
            id=scenario.chain_id,
            instrument="6501.T",
            next_update_policy="incremental_allowed",
            current_revision=SimpleNamespace(
                id=f"baseline-{scenario.scenario}",
                cutoff=date(2026, 8, 9),
            ),
        )
        for scenario in scenarios
    }
    verification_count = 0

    def verify_source_checkout() -> None:
        nonlocal verification_count
        verification_count += 1
        if verification_count == 2:
            raise LiveThesisValidationError("source checkout changed")

    class SourceChangedService:
        settings = SimpleNamespace(research_update_mode="shadow")

        def get_research_chain(self, chain_id):
            return chains[chain_id]

        def backup_database(self, destination: Path) -> Path:
            with sqlite3.connect(destination) as connection:
                connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
                connection.execute("INSERT INTO alembic_version VALUES ('fixture')")
            return destination

        def run_chain_update(self, *_args, **_kwargs):
            raise AssertionError("execution must not start after source changes")

    with pytest.raises(LiveThesisValidationError, match="source checkout changed"):
        validate_live_thesis(
            SourceChangedService(),
            scenarios,
            backup_destination=tmp_path / "backup.db",
            manifest_root=tmp_path / "manifest",
            git_commit="a" * 40,
            environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
            in_place_database=True,
            verify_source_checkout=verify_source_checkout,
            validate_market_readiness=lambda _request: None,
        )

    assert verification_count == 2
    assert (tmp_path / "backup.db").is_file()


def test_validation_writes_authoritative_main_database_and_only_sanitized_manifest(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "tradingagents.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('0009_outcome_feedback')")
        connection.execute(
            "CREATE TABLE validation_fixture (run_id TEXT PRIMARY KEY, secret_payload TEXT)"
        )
    scenarios = _loaded_scenarios(tmp_path)
    by_chain = {scenario.chain_id: scenario for scenario in scenarios}
    actual_full_conclusions = {
        scenario.scenario: scenario.expected_full_change_conclusion for scenario in scenarios
    }
    chains = {
        scenario.chain_id: SimpleNamespace(
            id=scenario.chain_id,
            instrument="6501.T",
            next_update_policy="incremental_allowed",
            current_revision=SimpleNamespace(
                id=f"baseline-{scenario.scenario}",
                cutoff=date(2026, 8, 9),
            ),
        )
        for scenario in scenarios
    }
    runs = {}
    run_sequence = [0]
    enqueue_failures: set[str] = set()
    cancelled_scenarios: set[str] = set()
    omit_semantic_assessment = [False]

    class Repository:
        def get_research_chain(self, chain_id):
            return chains[chain_id]

        def claim_run(self, run_id, _worker_id, _lease_seconds):
            return runs[run_id]

        def get_run(self, run_id):
            return runs[run_id]

    class MainDatabaseService:
        settings = SimpleNamespace(
            research_update_mode="shadow",
            database_path=database_path,
            lease_seconds=30,
        )
        repository = Repository()

        def get_research_chain(self, chain_id):
            return self.repository.get_research_chain(chain_id)

        def run_chain_update(
            self,
            chain_id,
            baseline_revision_id,
            request,
            *,
            idempotency_key,
        ):
            scenario = by_chain[chain_id]
            if scenario.scenario in enqueue_failures:
                raise RuntimeError("fixture enqueue failure")
            run = self.enqueue_chain_update(
                chain_id,
                baseline_revision_id,
                request,
                idempotency_key=idempotency_key,
            )
            if scenario.scenario in cancelled_scenarios:
                run.status = RunStatus.CANCELLED
                run.metrics = RunMetrics()
                return run, SimpleNamespace(status=RunStatus.CANCELLED)
            claimed = self.repository.claim_run(run.id, "fixture-worker", 30)
            result = self.execute_claimed(claimed, worker_id="fixture-worker")
            return run, result

        def backup_database(self, destination: Path) -> Path:
            with (
                sqlite3.connect(database_path) as source,
                sqlite3.connect(destination) as target,
            ):
                source.backup(target)
            return destination

        def enqueue_chain_update(
            self,
            chain_id,
            baseline_revision_id,
            _request,
            *,
            idempotency_key,
        ):
            assert idempotency_key.startswith("live-thesis:")
            run_sequence[0] += 1
            run = SimpleNamespace(
                id=f"run-{by_chain[chain_id].scenario}-{run_sequence[0]}",
                chain_id=chain_id,
                baseline_revision_id=baseline_revision_id,
                status=RunStatus.RUNNING,
                research_update_audit=None,
            )
            runs[run.id] = run
            return run

        def execute_claimed(self, run, *, worker_id):
            assert worker_id == "fixture-worker"
            scenario = by_chain[run.chain_id]
            bounded = RunMetrics(tool_calls=2, wall_time_seconds=0.25)
            full = RunMetrics(
                llm_calls=2,
                input_tokens=120,
                output_tokens=30,
                cache_hit_input_tokens=20,
                detailed_usage_calls=2,
                cost_usd=0.04,
                wall_time_seconds=0.75,
            )
            metrics = merge_run_metrics(bounded, full)
            is_candidate = scenario.expected_bounded_result == "no_material_change"
            actual_conclusion = actual_full_conclusions[scenario.scenario]
            comparison = (
                "inconclusive"
                if is_candidate and actual_conclusion is ResearchChangeConclusion.INDETERMINATE
                else "agreement"
                if is_candidate and actual_conclusion is ResearchChangeConclusion.NO_MATERIAL_CHANGE
                else "disagreement"
                if is_candidate
                else "not_applicable"
            )
            audit = SimpleNamespace(
                mode="shadow",
                authoritative_strategy="full",
                candidate=(
                    SimpleNamespace(change_conclusion="no_material_change")
                    if is_candidate
                    else None
                ),
                escalation_reason=(None if is_candidate else scenario.expected_bounded_result),
                comparison=comparison,
                bounded_metrics=bounded,
                full_metrics=full,
                semantic_assessment=(
                    None
                    if omit_semantic_assessment[0]
                    or not scenario.expected_bounded_result.startswith("semantic_")
                    else object()
                ),
                evidence="Fixture Evidence that must stay in SQLite",
            )
            revision = SimpleNamespace(
                id=f"revision-{scenario.scenario}",
                predecessor_revision_id=run.baseline_revision_id,
                producing_run_id=run.id,
                change_conclusion=actual_conclusion,
                metrics=metrics,
                cutoff=scenario.analysis_date,
            )
            chains[run.chain_id].current_revision = revision
            run.status = RunStatus.SUCCEEDED
            run.research_update_audit = audit
            run.metrics = metrics
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "INSERT INTO validation_fixture VALUES (?, ?)",
                    (run.id, "secret prompt and Evidence payload"),
                )
            return SimpleNamespace(status=RunStatus.SUCCEEDED, metrics=metrics)

    backup = tmp_path / "recovery.db"
    verify_source_checkout = Mock()
    result = validate_live_thesis(
        MainDatabaseService(),
        scenarios,
        backup_destination=backup,
        manifest_root=tmp_path / "manifest",
        git_commit="a" * 40,
        environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
        in_place_database=True,
        verify_source_checkout=verify_source_checkout,
        validate_market_readiness=lambda _request: None,
    )

    assert result.passed
    assert verify_source_checkout.call_count == 11
    assert {entry.validation_verdict for entry in result.entries} == {"passed"}
    assert {entry.expected_full_change_conclusion for entry in result.entries} == {
        ResearchChangeConclusion.MATERIAL_CHANGE,
        ResearchChangeConclusion.NO_MATERIAL_CHANGE,
        ResearchChangeConclusion.INDETERMINATE,
    }
    assert runs["run-quiet_interval-1"].research_update_audit.comparison == "inconclusive"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM validation_fixture").fetchone()[0] == 5
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM validation_fixture").fetchone()[0] == 0
    manifest_files = sorted(result.manifest_directory.glob("*.json"))
    assert len(manifest_files) == 6
    entry_payload = json.loads(manifest_files[1].read_text(encoding="utf-8"))
    assert entry_payload["git_commit"] == "a" * 40
    assert set(entry_payload) == {
        "application_status",
        "chain_id",
        "expected_bounded_result",
        "expected_full_change_conclusion",
        "git_commit",
        "revision_id",
        "run_id",
        "scenario",
        "schema_version",
        "validation_verdict",
    }
    serialized_manifest = "\n".join(path.read_text(encoding="utf-8") for path in manifest_files)
    assert "Fixture Evidence" not in serialized_manifest
    assert "secret prompt" not in serialized_manifest
    assert "metrics" not in serialized_manifest
    assert "diff" not in serialized_manifest

    actual_full_conclusions["threshold_crossing"] = ResearchChangeConclusion.NO_MATERIAL_CHANGE
    enqueue_failures.add("source_integrity")
    cancelled_scenarios.add("missing_coverage")
    omit_semantic_assessment[0] = True
    later_scenarios = tuple(
        item.model_copy(update={"analysis_date": item.analysis_date + timedelta(days=1)})
        for item in scenarios
    )
    mismatch = validate_live_thesis(
        MainDatabaseService(),
        later_scenarios,
        backup_destination=tmp_path / "recovery-2.db",
        manifest_root=tmp_path / "manifest",
        git_commit="b" * 40,
        environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
        in_place_database=True,
        verify_source_checkout=lambda: None,
        validate_market_readiness=lambda _request: None,
    )
    mismatch_entry = next(
        item for item in mismatch.entries if item.scenario == "threshold_crossing"
    )
    semantic_entry = next(
        item for item in mismatch.entries if item.scenario == "material_event"
    )
    enqueue_entry = next(
        item for item in mismatch.entries if item.scenario == "source_integrity"
    )
    cancelled_entry = next(
        item for item in mismatch.entries if item.scenario == "missing_coverage"
    )
    assert not mismatch.passed
    assert mismatch_entry.application_status == "succeeded"
    assert mismatch_entry.validation_verdict == "expectation_mismatch"
    assert semantic_entry.validation_verdict == "expectation_mismatch"
    assert enqueue_entry.application_status == "failed"
    assert enqueue_entry.validation_verdict == "application_failed"
    assert enqueue_entry.run_id is None
    assert cancelled_entry.application_status == "cancelled"
    assert cancelled_entry.validation_verdict == "application_failed"
    assert len(mismatch.entries) == 5
