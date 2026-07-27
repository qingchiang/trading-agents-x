from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import (
    AnalysisRequest,
    EvidenceBundle,
    EvidenceItem,
    ResearchDecision,
    ResearchRating,
    RunProfile,
)
from tradingagents.application.settings import AppSettings


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
    assert resolved.output_language == "Chinese"
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
