from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from tests.support.factories import analyst_report, research_decision
from tradingagents import RunProfile as PublicRunProfile
from tradingagents.configuration.settings import AppSettings, RunSettings
from tradingagents.domain.common import (
    ReportLanguage,
    ResearchConfidenceLevel,
    RunProfile,
    RunStatus,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.domain.reports import ResearchWarning
from tradingagents.domain.runs import AnalysisRequest


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


def test_public_enum_contract_remains_stable() -> None:
    assert PublicRunProfile is RunProfile
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
    assert str(RunProfile.DEEP) == "RunProfile.DEEP"

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


def test_research_decision_confidence_uses_fixed_research_levels() -> None:
    decision = research_decision(confidence=ResearchConfidenceLevel.MEDIUM)

    assert decision.confidence is ResearchConfidenceLevel.MEDIUM
    assert decision.model_dump(mode="json")["confidence"] == "medium"
    assert ResearchDecision.model_json_schema()["$defs"]["ResearchConfidenceLevel"][
        "enum"
    ] == ["low", "medium", "high"]

    payload = decision.model_dump(mode="json")
    payload["confidence"] = 0.65
    with pytest.raises(ValidationError, match="confidence"):
        ResearchDecision.model_validate(payload)


def test_research_decision_rejects_account_level_fields() -> None:
    payload = research_decision().model_dump(mode="json")
    payload["position_size"] = 0.25

    with pytest.raises(ValidationError, match="position_size"):
        ResearchDecision.model_validate(payload)


def test_research_decision_accepts_nonpersonalized_reference_levels() -> None:
    payload = research_decision().model_dump(mode="json")
    payload.update(
        {
            "rating": "Overweight",
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
    assert "valuation_assessment" not in decision.model_dump()
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


def test_environment_is_imported_only_from_explicit_mapping(tmp_path) -> None:
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

    from tests.support.configuration_helpers import import_configuration
    _, resolved = import_configuration(settings).resolve_request(request)

    assert settings.host == "0.0.0.0"
    assert resolved.deep_binding.connection.compatibility == "deepseek"
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

    from tests.support.configuration_helpers import import_configuration
    from tradingagents.configuration.models import ConfigurationPatch
    assert import_configuration(defaults).read().values.trash_retention_days == 30
    assert import_configuration(disabled).read().values.trash_retention_days == 0
    with pytest.raises(ValueError):
        ConfigurationPatch(revision=0, values={"trash_retention_days": -1})


def test_legacy_archive_retention_is_reported_during_import(tmp_path):
    from tradingagents.configuration.models import ImportRequest
    from tradingagents.persistence import upgrade_database
    from tradingagents.persistence.configuration import ConfigurationStore
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path), "TRADINGAGENTS_ARCHIVE_RETENTION_DAYS": "30"})
    upgrade_database(settings)
    preview = ConfigurationStore(settings).preview_import(ImportRequest())
    assert [issue.name for issue in preview.issues] == ["TRADINGAGENTS_ARCHIVE_RETENTION_DAYS"]


def test_request_overrides_role_specific_imported_defaults(tmp_path) -> None:
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
        models={"deep": {"model": "deep-request", "reasoning_effort": "max"}},
    )

    from tests.support.configuration_helpers import import_configuration
    _, resolved = import_configuration(settings).resolve_request(request)

    assert resolved.quick_binding.model == "quick-default"
    assert resolved.deep_binding.model == "deep-request"
    assert resolved.quick_binding.reasoning_effort == "low"
    assert resolved.deep_binding.reasoning_effort == "max"


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


def test_omitted_request_values_inherit_and_materialize_imported_defaults(
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

    from tests.support.configuration_helpers import import_configuration
    materialized, resolved = import_configuration(settings).resolve_request(request)

    assert resolved.output_language == custom_language
    assert resolved.snapshot()["output_language"] == custom_language
    assert (
        resolved.dataflow_config(settings)["output_language"]
        == custom_language
    )
    assert materialized.output_language == custom_language
    assert materialized.models.quick.reasoning_effort == "low"
    assert materialized.models.deep.reasoning_effort == "high"
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
