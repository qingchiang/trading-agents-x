from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalystReport,
    EvidenceBundle,
    EvidenceItem,
    ReportLanguage,
    ResearchDecision,
    ResearchRating,
    ResearchWarning,
    RunProfile,
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


def test_research_decision_rejects_account_level_fields() -> None:
    payload = {
        "rating": ResearchRating.HOLD,
        "confidence": 0.6,
        "thesis": "Valuation and growth evidence are balanced.",
        "time_horizon": "6-12 months",
        "position_size": 0.25,
    }

    with pytest.raises(ValidationError, match="position_size"):
        ResearchDecision.model_validate(payload)


def test_legacy_warning_strings_become_plain_structured_records() -> None:
    report = AnalystReport(
        analyst="market",
        summary="Summary",
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
        available_at=datetime(2026, 7, 25, 5, tzinfo=timezone.utc),
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
            "TRADINGAGENTS_PROVENANCE_APPENDIX": "true",
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
    assert "provenance_appendix" not in resolved.snapshot()["data_config"]
    assert resolved.dataflow_config(settings)["provenance_appendix"] is False


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
