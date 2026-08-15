from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    EvidenceBundle,
    EvidenceItem,
    ReportLanguage,
    ResearchDecision,
    ResearchWarning,
    RunProfile,
    RunStatus,
)
from tradingagents.application.settings import AppSettings, RunSettings


def test_analysis_request_is_normalized_ordered_and_immutable() -> None:
    request = AnalysisRequest(
        ticker=" 7203.t ",
        analysis_date="2026-07-24",
        profile=RunProfile.DEEP,
        analysts=("news", "market"),
        output_language="ja",
    )

    assert request.ticker == "7203.T"
    assert request.analysts == ("market", "news")
    with pytest.raises(ValidationError):
        request.ticker = "NVDA"


def test_public_enum_values_and_json_schema_remain_stable() -> None:
    assert {member.name: member.value for member in RunProfile} == {
        "FAST": "fast",
        "STANDARD": "standard",
        "DEEP": "deep",
    }
    assert {member.name: member.value for member in RunStatus} == {
        "QUEUED": "queued",
        "RUNNING": "running",
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
    }

    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        profile=RunProfile.DEEP,
    )

    assert request.model_dump(mode="json")["profile"] == "deep"
    assert AnalysisRequest.model_json_schema()["$defs"]["RunProfile"]["enum"] == [
        "fast",
        "standard",
        "deep",
    ]


def test_research_decision_rejects_account_level_fields() -> None:
    payload = research_decision().model_dump(mode="json")
    payload["position_size"] = 0.25

    with pytest.raises(ValidationError, match="position_size"):
        ResearchDecision.model_validate(payload)


def test_research_decision_accepts_audited_nonpersonalized_opinions() -> None:
    payload = research_decision().model_dump(mode="json")
    payload.update(
        {
            "rating": "Overweight",
            "valuation_assessment": {
                "method": "Comparable multiples",
                "low": {
                    "value": 90.0,
                        "basis": "derived",
                        "evidence_refs": ["ev_0123456789ab"],
                        "date_evidence_refs": ["ev_0123456789ab"],
                        "calculation_id": "calc_valuation_low",
                    "as_of_date": "2026-07-24",
                    "temporal_basis": "point_in_time",
                },
                "high": {
                    "value": 110.0,
                        "basis": "derived",
                        "evidence_refs": ["ev_0123456789ab"],
                        "date_evidence_refs": ["ev_0123456789ab"],
                        "calculation_id": "calc_valuation_high",
                    "as_of_date": "2026-07-24",
                    "temporal_basis": "point_in_time",
                },
                "measurement_kind": "currency",
                "unit": "USD",
                "limitations": ["Peer comparability is imperfect."],
            },
            "market_reference_levels": [
                {
                    "label": "Observed support",
                    "value": 95.0,
                    "unit": "USD",
                    "as_of_date": "2026-07-24",
                    "interpretation": (
                        "An observed reference level, not an execution order."
                    ),
                    "evidence_refs": ["ev_0123456789ab"],
                    "date_evidence_refs": ["ev_0123456789ab"],
                    "source_locator": {"evidence_ref": "ev_0123456789ab"},
            }
            ],
        }
    )

    decision = ResearchDecision.model_validate(payload)

    assert decision.rating.value == "Overweight"
    assert decision.valuation_assessment is not None
    assert decision.market_reference_levels[0].label == "Observed support"


def test_research_decision_merges_nested_evidence_refs_deterministically() -> None:
    payload = research_decision().model_dump(mode="json")
    nested_ref = "ev_ffffffffffff"
    payload["scenarios"][0]["evidence_refs"] = [nested_ref]
    payload["evidence_refs"] = ["ev_0123456789ab"]

    decision = ResearchDecision.model_validate(payload)

    assert decision.evidence_refs == (
        "ev_0123456789ab",
        nested_ref,
    )


def test_research_decision_rejects_duplicate_scenario_kinds() -> None:
    payload = research_decision().model_dump(mode="json")
    payload["scenarios"][1]["kind"] = "base"

    with pytest.raises(
        ValidationError,
        match="decision_scenarios_duplicate_kind",
    ):
        ResearchDecision.model_validate(payload)


def test_legacy_warning_strings_become_plain_structured_records() -> None:
    report = analyst_report(
        executive_summary="Summary",
        confidence=0.5,
        warnings=("**Historical source** was `partial`.",),
        narrative="Narrative",
    )

    assert report.warnings == (
        ResearchWarning(
            code="legacy.warning",
            message="Historical source was partial.",
        ),
    )


def test_evidence_bundle_rejects_future_information() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="filing",
        requested_date=date(2026, 7, 24),
        available_at=datetime(2026, 7, 25, 5, tzinfo=UTC),
        content="Not yet available at the analysis cutoff.",
    )

    with pytest.raises(ValidationError, match="available_at"):
        EvidenceBundle(
            instrument="NVDA",
            analysis_date=date(2026, 7, 24),
            items=(item,),
        )


def test_environment_is_loaded_only_from_explicit_mapping(tmp_path) -> None:
    settings = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_LLM_PROVIDER": "deepseek",
            "TRADINGAGENTS_OUTPUT_LANGUAGE": "ja",
            "TRADINGAGENTS_LAN_ENABLED": "true",
            "TRADINGAGENTS_LAN_TOKEN": "do-not-persist",
        },
        load_env_files=False,
    )
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        output_language="zh-Hans",
    )

    resolved = settings.resolve_run(request)

    assert settings.host == "0.0.0.0"
    assert resolved.llm_provider == "deepseek"
    assert resolved.output_language is ReportLanguage.SIMPLIFIED_CHINESE
    assert resolved.output_language.prompt_label == (
        "Simplified Chinese (简体中文, zh-CN)"
    )
    assert "zh-Hans" not in str(resolved.snapshot())
    assert "do-not-persist" not in str(resolved.snapshot())


def test_lan_mode_requires_token(tmp_path) -> None:
    with pytest.raises(ValueError, match="LAN_TOKEN"):
        AppSettings.from_env(
            environ={
                "TRADINGAGENTS_HOME": str(tmp_path),
                "TRADINGAGENTS_LAN_ENABLED": "true",
            },
            load_env_files=False,
        )


def test_trash_retention_defaults_can_be_disabled_and_reject_negatives(
    tmp_path,
) -> None:
    defaults = AppSettings.from_env(
        environ={"TRADINGAGENTS_HOME": str(tmp_path / "defaults")},
        load_env_files=False,
    )
    disabled = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path / "disabled"),
            "TRADINGAGENTS_TRASH_RETENTION_DAYS": "0",
        },
        load_env_files=False,
    )

    assert defaults.trash_retention_days == 30
    assert disabled.trash_retention_days == 0
    with pytest.raises(ValueError, match="must be >= 0"):
        AppSettings.from_env(
            environ={
                "TRADINGAGENTS_HOME": str(tmp_path / "invalid"),
                "TRADINGAGENTS_TRASH_RETENTION_DAYS": "-1",
            },
            load_env_files=False,
        )


def test_incremental_research_experiment_is_off_by_default_and_has_no_whitelist_surface(
    tmp_path,
) -> None:
    defaults = AppSettings.from_env(
        environ={"TRADINGAGENTS_HOME": str(tmp_path / "defaults")},
        load_env_files=False,
    )
    enabled = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path / "enabled"),
            "TRADINGAGENTS_RESEARCH_UPDATE_MODE": "experimental",
            "TRADINGAGENTS_EXPERIMENTAL_NMC_JP_WHITELIST": " 6501.t,7203.T,6501.T ",
        },
        load_env_files=False,
    )

    assert defaults.research_update_mode == "off"
    assert enabled.research_update_mode == "experimental"
    assert "experimental_nmc_jp_whitelist" not in enabled.model_fields
    assert "experimental_nmc_jp_whitelist" not in enabled.default_run_settings.snapshot()


def test_old_run_snapshot_whitelist_is_readable_but_ignored() -> None:
    restored = RunSettings.model_validate(
        {
            "research_update_mode": "experimental",
            "experimental_nmc_jp_whitelist": ["6501.T"],
            "data_config": {"news_article_limit": 30},
        }
    )

    assert restored.research_update_mode == "experimental"
    assert "experimental_nmc_jp_whitelist" not in restored.snapshot()


def test_legacy_archive_retention_setting_fails_with_rename_guidance(
    tmp_path,
) -> None:
    with pytest.raises(
        ValueError,
        match="TRADINGAGENTS_TRASH_RETENTION_DAYS",
    ):
        AppSettings.from_env(
            environ={
                "TRADINGAGENTS_HOME": str(tmp_path),
                "TRADINGAGENTS_ARCHIVE_RETENTION_DAYS": "30",
            },
            load_env_files=False,
        )


def test_request_overrides_role_specific_environment_defaults(tmp_path) -> None:
    settings = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_LLM_PROVIDER": "openai",
            "TRADINGAGENTS_QUICK_THINK_LLM": "quick-default",
            "TRADINGAGENTS_DEEP_THINK_LLM": "deep-default",
            "TRADINGAGENTS_QUICK_REASONING_EFFORT": "low",
            "TRADINGAGENTS_DEEP_REASONING_EFFORT": "high",
        },
        load_env_files=False,
    )
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        deep_model="deep-request",
        deep_reasoning_effort="max",
    )

    resolved = settings.resolve_run(request)

    assert resolved.quick_model == "quick-default"
    assert resolved.deep_model == "deep-request"
    assert resolved.quick_reasoning_effort == "low"
    assert resolved.deep_reasoning_effort == "max"


@pytest.mark.parametrize(
    "legacy",
    [
        "zh-Hans",
        "Chinese",
        "simplified chinese",
        "简体中文",
    ],
)
def test_legacy_simplified_chinese_aliases_normalize_to_zh_cn(legacy) -> None:
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        output_language=legacy,
    )

    assert request.output_language is ReportLanguage.SIMPLIFIED_CHINESE
    assert request.model_dump(mode="json")["output_language"] == "zh-CN"


@pytest.mark.parametrize(
    "custom",
    [
        "cn",
        "Simplified Chinese (简体中文, zh-CN)",
        "Write all prose in concise Simplified Chinese; retain source names.",
    ],
)
def test_custom_output_language_instructions_are_preserved(custom) -> None:
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        output_language=f"  {custom}  ",
    )

    assert request.output_language == custom
    assert request.model_dump(mode="json")["output_language"] == custom


def test_empty_output_language_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            output_language="  ",
        )


def test_omitted_request_values_inherit_and_materialize_environment_defaults(
    tmp_path,
) -> None:
    custom_language = "Simplified Chinese (简体中文, zh-CN)"
    settings = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_OUTPUT_LANGUAGE": custom_language,
            "TRADINGAGENTS_QUICK_REASONING_EFFORT": "low",
            "TRADINGAGENTS_DEEP_REASONING_EFFORT": "high",
        },
        load_env_files=False,
    )
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")

    resolved = settings.resolve_run(request)
    materialized = settings.materialize_request(
        request,
        run_settings=resolved,
    )

    assert resolved.output_language == custom_language
    assert resolved.snapshot()["output_language"] == custom_language
    assert (
        resolved.dataflow_config(settings)["output_language"]
        == custom_language
    )
    assert materialized.output_language == custom_language
    assert materialized.quick_reasoning_effort == "low"
    assert materialized.deep_reasoning_effort == "high"
    assert "provenance" not in materialized.model_dump(mode="json")
    assert "provenance" not in resolved.snapshot()


def test_removed_provenance_options_are_rejected() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        AnalysisRequest.model_validate(
            {
                "ticker": "NVDA",
                "analysis_date": "2026-07-24",
                "provenance": True,
            }
        )
    with pytest.raises(ValidationError, match="provenance"):
        RunSettings.model_validate(
            {
                "provenance": True,
                "data_config": {"news_article_limit": 30},
            }
        )
