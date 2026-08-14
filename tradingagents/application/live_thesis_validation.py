"""Maintainer-triggered validation of authoritative Live Thesis updates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from .contracts import AnalysisRequest, RunStatus
from .metrics import merge_run_metrics
from .research import (
    ResearchChangeConclusion,
    derive_shadow_comparison_from_conclusions,
)
from .service import ChainUpdateExecutionError

ValidationScenario = Literal[
    "quiet_interval",
    "material_event",
    "source_integrity",
    "missing_coverage",
    "threshold_crossing",
]

_REQUIRED_SCENARIOS = {
    "quiet_interval",
    "material_event",
    "source_integrity",
    "missing_coverage",
    "threshold_crossing",
}
_SCENARIO_BOUNDED_RESULTS = {
    "quiet_interval": {"no_material_change"},
    "material_event": {
        "source_version_change",
        "semantic_weakening",
        "semantic_contradiction",
        "semantic_answering",
        "semantic_reopening",
        "potentially_material_novelty",
        "confidence_change",
    },
    "source_integrity": {
        "source_correction",
        "source_withdrawal",
        "source_replacement",
    },
    "missing_coverage": {"coverage_incomplete"},
    "threshold_crossing": {"threshold_crossing"},
}
_SEMANTIC_BOUNDED_RESULTS = {
    "semantic_weakening",
    "semantic_contradiction",
    "semantic_answering",
    "semantic_reopening",
    "potentially_material_novelty",
    "confidence_change",
}


class LiveThesisValidationError(ValueError):
    """Raised before or during an invalid controlled validation request."""


class ReviewedLiveThesisScenario(BaseModel):
    """One preselected Chain and its reviewed validation expectations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: ValidationScenario
    chain_id: str
    analysis_date: date
    expected_bounded_result: str
    expected_full_change_conclusion: ResearchChangeConclusion


class BackupRecoveryPoint(BaseModel):
    """Non-sensitive identity for the verified ordinary SQLite backup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    created_at: datetime
    backup_file: str
    sha256: str
    size_bytes: int
    alembic_revision: str


class LiveThesisManifestEntry(BaseModel):
    """Sanitized experiment metadata not already owned by SQLite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    git_commit: str
    scenario: ValidationScenario
    expected_bounded_result: str
    expected_full_change_conclusion: ResearchChangeConclusion
    application_status: Literal["succeeded", "failed", "cancelled"]
    validation_verdict: Literal["passed", "expectation_mismatch", "application_failed"]
    run_id: str | None
    chain_id: str
    revision_id: str | None


class LiveThesisValidationResult(BaseModel):
    """Result returned to the maintainer-facing CLI."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_directory: Path
    recovery_point: BackupRecoveryPoint
    entries: tuple[LiveThesisManifestEntry, ...]

    @property
    def passed(self) -> bool:
        return all(item.validation_verdict == "passed" for item in self.entries)


def load_reviewed_scenarios(path: Path) -> tuple[ReviewedLiveThesisScenario, ...]:
    """Load the strict five-case maintainer review set without secret fields."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise LiveThesisValidationError("reviewed scenarios must be a JSON list")
        scenarios = tuple(ReviewedLiveThesisScenario.model_validate(item) for item in raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise LiveThesisValidationError("reviewed scenarios are invalid") from exc
    _validate_reviewed_scenarios(scenarios)
    return scenarios


def _validate_reviewed_scenarios(
    scenarios: Sequence[ReviewedLiveThesisScenario],
) -> None:
    names = [item.scenario for item in scenarios]
    if len(names) != len(_REQUIRED_SCENARIOS) or set(names) != _REQUIRED_SCENARIOS:
        raise LiveThesisValidationError("exactly one of each reviewed scenario is required")
    if len({item.chain_id for item in scenarios}) != len(scenarios):
        raise LiveThesisValidationError("every scenario requires a distinct Research Chain")
    if {item.expected_full_change_conclusion for item in scenarios} != set(
        ResearchChangeConclusion
    ):
        raise LiveThesisValidationError(
            "reviewed scenarios must include every Full Change Conclusion"
        )
    for item in scenarios:
        if item.expected_bounded_result not in _SCENARIO_BOUNDED_RESULTS[item.scenario]:
            raise LiveThesisValidationError(f"bounded result is not reviewed for {item.scenario}")


def validate_live_thesis(
    service: Any,
    scenarios: Sequence[ReviewedLiveThesisScenario],
    *,
    backup_destination: Path,
    manifest_root: Path,
    git_commit: str,
    environ: Mapping[str, str],
    in_place_database: bool,
    verify_source_checkout: Callable[[], None],
) -> LiveThesisValidationResult:
    """Run the reviewed Shadow set after backup and retain only sanitized metadata."""
    if environ.get("RUN_LIVE_DATA_TESTS") != "1":
        raise LiveThesisValidationError("RUN_LIVE_DATA_TESTS=1 is required")
    if environ.get("RUN_LIVE_LLM_TESTS") != "1":
        raise LiveThesisValidationError("RUN_LIVE_LLM_TESTS=1 is required")
    if not in_place_database:
        raise LiveThesisValidationError("explicit in-place database opt-in is required")
    _validate_reviewed_scenarios(scenarios)
    if service.settings.research_update_mode != "shadow":
        raise LiveThesisValidationError("research update mode must be shadow")
    if len(git_commit) != 40 or any(
        character not in "0123456789abcdef" for character in git_commit
    ):
        raise LiveThesisValidationError("git commit must be a full lowercase SHA-1")
    selected_chains: dict[str, Any] = {}
    selected_requests: dict[str, AnalysisRequest] = {}
    for scenario in scenarios:
        try:
            chain = service.get_research_chain(scenario.chain_id)
        except Exception as exc:
            raise LiveThesisValidationError("reviewed Research Chain was not found") from exc
        if chain.next_update_policy != "incremental_allowed":
            raise LiveThesisValidationError(
                f"reviewed Research Chain {chain.id} is not an Eligible Baseline"
            )
        if (
            chain.current_revision is None
            or scenario.analysis_date <= chain.current_revision.cutoff
        ):
            raise LiveThesisValidationError(
                f"reviewed cutoff must be later than Research Chain {chain.id} head"
            )
        selected_chains[scenario.chain_id] = chain
        selected_requests[scenario.chain_id] = AnalysisRequest(
            ticker=chain.instrument,
            analysis_date=scenario.analysis_date,
        )
    for scenario in scenarios:
        try:
            service.validate_market_data_readiness(
                selected_requests[scenario.chain_id]
            )
        except Exception as exc:
            raise LiveThesisValidationError(
                f"reviewed market data is not ready for {scenario.scenario}"
            ) from exc
    verify_source_checkout()
    if backup_destination.exists():
        raise LiveThesisValidationError("backup destination already exists")
    try:
        created_backup = service.backup_database(backup_destination)
    except Exception as exc:
        raise LiveThesisValidationError("ordinary SQLite backup failed") from exc
    try:
        recovery_point = _verified_recovery_point(created_backup)
        manifest_directory = _create_manifest_directory(
            manifest_root,
            recovery_point=recovery_point,
            git_commit=git_commit,
        )
        _write_json_exclusive(
            manifest_directory / "recovery-point.json",
            recovery_point.model_dump(mode="json"),
        )
    except Exception as exc:
        raise LiveThesisValidationError(
            "backup verification or recovery metadata recording failed"
        ) from exc

    entries: list[LiveThesisManifestEntry] = []
    for index, scenario in enumerate(scenarios, start=1):
        verify_source_checkout()
        baseline = selected_chains[scenario.chain_id].current_revision
        run_id: str | None = None
        try:
            run, result = service.run_chain_update(
                scenario.chain_id,
                baseline.id,
                selected_requests[scenario.chain_id],
                idempotency_key=(
                    f"live-thesis:{manifest_directory.name}:{scenario.scenario}:{uuid4()}"
                ),
            )
            run_id = run.id
            entry = (
                _successful_entry(
                    service,
                    scenario,
                    baseline_revision_id=baseline.id,
                    run_id=run_id,
                    git_commit=git_commit,
                )
                if result.status is RunStatus.SUCCEEDED
                else _failed_entry(
                    service,
                    scenario,
                    run_id=run_id,
                    git_commit=git_commit,
                )
            )
        except ChainUpdateExecutionError as exc:
            run_id = exc.run_id
            entry = _failed_entry(service, scenario, run_id=run_id, git_commit=git_commit)
        except Exception:
            entry = _failed_entry(service, scenario, run_id=run_id, git_commit=git_commit)
        verify_source_checkout()
        _write_json_exclusive(
            manifest_directory
            / f"{index:02d}-{scenario.scenario}-{run_id or f'no-run-{uuid4()}'}.json",
            entry.model_dump(mode="json"),
        )
        entries.append(entry)
    return LiveThesisValidationResult(
        manifest_directory=manifest_directory,
        recovery_point=recovery_point,
        entries=tuple(entries),
    )


def _verified_recovery_point(path: Path) -> BackupRecoveryPoint:
    backup = path.expanduser().resolve()
    if not backup.is_file() or backup.stat().st_size <= 0:
        raise LiveThesisValidationError("backup file is missing or empty")
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise LiveThesisValidationError("backup integrity check failed")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision is None:
            raise LiveThesisValidationError("backup has no Alembic revision")
    digest = hashlib.sha256()
    with backup.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return BackupRecoveryPoint(
        created_at=datetime.now(timezone.utc),
        backup_file=backup.name,
        sha256=digest.hexdigest(),
        size_bytes=backup.stat().st_size,
        alembic_revision=str(revision[0]),
    )


def _create_manifest_directory(
    root: Path,
    *,
    recovery_point: BackupRecoveryPoint,
    git_commit: str,
) -> Path:
    timestamp = recovery_point.created_at.strftime("%Y%m%dT%H%M%S%fZ")
    destination = root / f"{timestamp}-{git_commit[:8]}-{recovery_point.sha256[:8]}"
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _successful_entry(
    service: Any,
    scenario: ReviewedLiveThesisScenario,
    *,
    baseline_revision_id: str,
    run_id: str,
    git_commit: str,
) -> LiveThesisManifestEntry:
    completed = service.repository.get_run(run_id)
    chain = service.repository.get_research_chain(scenario.chain_id)
    revision = chain.current_revision
    audit = completed.research_update_audit
    bounded_result = None
    if audit is not None:
        bounded_result = (
            audit.candidate.change_conclusion
            if audit.candidate is not None
            else audit.escalation_reason
        )
    expected_comparison = derive_shadow_comparison_from_conclusions(
        (
            ResearchChangeConclusion.NO_MATERIAL_CHANGE
            if bounded_result == "no_material_change"
            else None
        ),
        revision.change_conclusion,
        candidate_present=bounded_result == "no_material_change",
    )
    reconciled_metrics = (
        merge_run_metrics(audit.bounded_metrics, audit.full_metrics) if audit is not None else None
    )
    passed = all(
        (
            completed.status is RunStatus.SUCCEEDED,
            revision.predecessor_revision_id == baseline_revision_id,
            revision.producing_run_id == run_id,
            audit is not None and audit.mode == "shadow",
            audit is not None and audit.authoritative_strategy == "full",
            bounded_result == scenario.expected_bounded_result,
            (
                audit is not None and audit.semantic_assessment is not None
                if bounded_result in _SEMANTIC_BOUNDED_RESULTS
                else True
            ),
            revision.change_conclusion is scenario.expected_full_change_conclusion,
            audit is not None and audit.comparison == expected_comparison,
            reconciled_metrics == completed.metrics,
            reconciled_metrics == revision.metrics,
        )
    )
    return LiveThesisManifestEntry(
        git_commit=git_commit,
        scenario=scenario.scenario,
        expected_bounded_result=scenario.expected_bounded_result,
        expected_full_change_conclusion=scenario.expected_full_change_conclusion,
        application_status="succeeded",
        validation_verdict="passed" if passed else "expectation_mismatch",
        run_id=run_id,
        chain_id=scenario.chain_id,
        revision_id=revision.id,
    )


def _failed_entry(
    service: Any,
    scenario: ReviewedLiveThesisScenario,
    *,
    run_id: str | None,
    git_commit: str,
) -> LiveThesisManifestEntry:
    status: Literal["failed", "cancelled"] = "failed"
    if run_id is not None:
        try:
            completed = service.repository.get_run(run_id)
        except Exception:
            pass
        else:
            if completed.status is RunStatus.CANCELLED:
                status = "cancelled"
    return LiveThesisManifestEntry(
        git_commit=git_commit,
        scenario=scenario.scenario,
        expected_bounded_result=scenario.expected_bounded_result,
        expected_full_change_conclusion=scenario.expected_full_change_conclusion,
        application_status=status,
        validation_verdict="application_failed",
        run_id=run_id,
        chain_id=scenario.chain_id,
        revision_id=None,
    )
