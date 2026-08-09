"""Opt-in Shadow comparisons for reviewed Japanese Research Chain heads.

The source database is opened only to create a SQLite backup. Each of the five
reviewed cases runs against its own temporary copy, so the live chain head is
never advanced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tradingagents.application.contracts import AnalysisRequest, RunStatus
from tradingagents.application.repository import RunRepository
from tradingagents.application.research import (
    IncrementalEscalationReason,
    ResearchChangeConclusion,
    derive_shadow_comparison_from_conclusions,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings

_REQUIRED_SCENARIOS = {
    "quiet_interval",
    "material_event",
    "source_integrity",
    "missing_coverage",
    "threshold_crossing",
}
_BOUNDED_RESULTS = {
    "no_material_change",
    *(reason.value for reason in IncrementalEscalationReason),
}
_FULL_OUTCOMES = {"material_change", "no_material_change", "indeterminate"}
_SCENARIO_RESULTS = {
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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_data,
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_DATA_TESTS") != "1" or os.environ.get("RUN_LIVE_LLM_TESTS") != "1",
        reason="Set RUN_LIVE_DATA_TESTS=1 and RUN_LIVE_LLM_TESTS=1",
    ),
]


def _reviewed_cases() -> tuple[dict[str, str], ...]:
    raw_cases = os.environ.get("TRADINGAGENTS_INCREMENTAL_LIVE_CASES")
    if not raw_cases:
        pytest.skip("Set TRADINGAGENTS_INCREMENTAL_LIVE_CASES to reviewed JSON cases")
    parsed = json.loads(raw_cases)
    assert isinstance(parsed, list) and len(parsed) == len(_REQUIRED_SCENARIOS)
    cases: list[dict[str, str]] = []
    required_keys = {
        "scenario",
        "chain_id",
        "analysis_date",
        "expected_bounded",
        "expected_full_outcome",
    }
    for value in parsed:
        assert isinstance(value, dict) and set(value) == required_keys
        case = {key: str(item) for key, item in value.items()}
        assert case["scenario"] in _REQUIRED_SCENARIOS
        assert case["expected_bounded"] in _BOUNDED_RESULTS
        assert case["expected_bounded"] in _SCENARIO_RESULTS[case["scenario"]]
        assert case["expected_full_outcome"] in _FULL_OUTCOMES
        cases.append(case)
    assert {case["scenario"] for case in cases} == _REQUIRED_SCENARIOS
    return tuple(cases)


def _isolated_settings(settings: AppSettings, root: Path) -> AppSettings:
    return settings.model_copy(
        update={
            "home_dir": root,
            "database_path": root / "tradingagents.db",
            "data_cache_dir": root / "cache",
        }
    )


def test_reviewed_japanese_shadow_pairs_are_sanitized_and_fail_closed(
    live_endpoint,
    tmp_path: Path,
) -> None:
    settings = AppSettings.from_env(environ=os.environ, load_env_files=False)
    assert settings.research_update_mode == "shadow"
    source_repository = RunRepository(settings)

    for index, case in enumerate(_reviewed_cases()):
        source_chain = source_repository.get_research_chain(case["chain_id"])
        assert source_chain.instrument in settings.experimental_nmc_jp_whitelist
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir()
        isolated = _isolated_settings(settings, case_root)
        source_repository.backup(isolated.database_path)
        service = AnalysisService(isolated)
        chain = service.repository.get_research_chain(case["chain_id"])
        request = AnalysisRequest(
            ticker=chain.instrument,
            analysis_date=case["analysis_date"],
        )
        queued = service.enqueue_chain_update(
            chain.id,
            chain.current_revision.id,
            request,
            idempotency_key=f"live-shadow:{case['scenario']}:{uuid4()}",
        )
        worker_id = f"live-shadow:{uuid4()}"
        claimed = service.repository.claim_run(
            queued.id,
            worker_id,
            isolated.lease_seconds,
        )
        with live_endpoint(
            f"incremental:{chain.instrument}:{case['scenario']}",
            source="bounded assessment + independent Full Analysis",
        ) as probe:
            result = service.execute_claimed(claimed, worker_id=worker_id)
            completed = service.repository.get_run(queued.id)
            audit = completed.research_update_audit
            assert result.status is RunStatus.SUCCEEDED
            assert audit is not None and audit.mode == "shadow"
            bounded = (
                audit.candidate.change_conclusion
                if audit.candidate is not None
                else audit.escalation_reason
            )
            revision = service.repository.get_research_chain(chain.id).current_revision
            probe.observe(
                source="bounded assessment + independent Full Analysis",
                last_observation=(
                    f"ticker={chain.instrument};cutoff={request.analysis_date};"
                    f"scenario={case['scenario']};bounded={bounded};"
                    f"full={revision.change_conclusion.value};comparison={audit.comparison};"
                    f"bounded_llm_calls={audit.bounded_metrics.llm_calls};"
                    f"full_llm_calls={audit.full_metrics.llm_calls}"
                ),
            )
        assert bounded == case["expected_bounded"]
        assert revision.change_conclusion.value == case["expected_full_outcome"]
        assert audit.full_metrics.llm_calls > 0
        if case["scenario"] == "quiet_interval":
            assert audit.semantic_assessment is not None
            assert audit.bounded_metrics.llm_calls > 0
        expected_comparison = derive_shadow_comparison_from_conclusions(
            (
                ResearchChangeConclusion.NO_MATERIAL_CHANGE
                if bounded == "no_material_change"
                else None
            ),
            revision.change_conclusion,
        )
        assert audit.comparison == expected_comparison
