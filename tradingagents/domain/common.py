"""Common contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]*$")


_EVIDENCE_REF_PATTERN = re.compile(r"^ev_[a-f0-9]{12}$")


_RESEARCH_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


_DECISION_COMPONENT_PATH_PATTERN = re.compile(
    r"^(?:executive_summary|thesis|catalysts\.\d+|risks\.\d+|"
    r"invalidation_conditions\.\d+|"
    r"scenarios\.(?:base|bull|bear)\.(?:outcome|core_assumptions\.\d+)|"
    r"risk_review_adjustments\.\d+\.explanation)$"
)


def _unique_evidence_refs(value: tuple[str, ...]) -> tuple[str, ...]:
    refs = tuple(dict.fromkeys(value))
    if any(not _EVIDENCE_REF_PATTERN.fullmatch(ref) for ref in refs):
        raise ValueError("invalid evidence reference")
    return refs


def _unique_research_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(value))
    if any(not _RESEARCH_ID_PATTERN.fullmatch(item) for item in ids):
        raise ValueError("invalid research identifier")
    return ids


def utc_now() -> datetime:
    """Return an aware UTC timestamp for public contracts."""
    return datetime.now(UTC)


def _deeply_frozen_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _deeply_frozen_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_deeply_frozen_value(item) for item in value)
    return value


def _deeply_frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _deeply_frozen_value(item) for key, item in value.items()})


class FrozenModel(BaseModel):
    """Base class for immutable public value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _StableStrEnum(StrEnum):
    """Use the standard string enum while retaining the prior ``str()`` contract."""

    __str__ = Enum.__str__


class RunProfile(_StableStrEnum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class ReportLanguage(_StableStrEnum):
    ENGLISH = "en"
    SIMPLIFIED_CHINESE = "zh-CN"
    JAPANESE = "ja"

    @property
    def prompt_label(self) -> str:
        return {
            ReportLanguage.ENGLISH: "English (en)",
            ReportLanguage.SIMPLIFIED_CHINESE: ("Simplified Chinese (简体中文, zh-CN)"),
            ReportLanguage.JAPANESE: "Japanese (日本語, ja)",
        }[self]


_REPORT_LANGUAGE_ALIASES = {
    "en": ReportLanguage.ENGLISH,
    "english": ReportLanguage.ENGLISH,
    "zh-cn": ReportLanguage.SIMPLIFIED_CHINESE,
    "zh-hans": ReportLanguage.SIMPLIFIED_CHINESE,
    "chinese": ReportLanguage.SIMPLIFIED_CHINESE,
    "simplified chinese": ReportLanguage.SIMPLIFIED_CHINESE,
    "简体中文": ReportLanguage.SIMPLIFIED_CHINESE,
    "ja": ReportLanguage.JAPANESE,
    "japanese": ReportLanguage.JAPANESE,
    "日本語": ReportLanguage.JAPANESE,
}


type OutputLanguage = ReportLanguage | str


def normalize_report_language(value: OutputLanguage) -> OutputLanguage:
    """Normalize simple language aliases while preserving custom instructions."""
    if isinstance(value, ReportLanguage):
        return value
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("output language must not be empty")
    return _REPORT_LANGUAGE_ALIASES.get(normalized.casefold(), normalized)


def report_language_value(value: OutputLanguage) -> str:
    """Return the durable request/config representation."""
    return value.value if isinstance(value, ReportLanguage) else value


def report_language_prompt_label(value: OutputLanguage) -> str:
    """Return a prompt-ready label without rewriting custom instructions."""
    return value.prompt_label if isinstance(value, ReportLanguage) else value


class RunStatus(_StableStrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunTrashState(_StableStrEnum):
    ACTIVE = "active"
    TRASHED = "trashed"
    ALL = "all"


class ResearchRating(_StableStrEnum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class ResearchConfidenceLevel(_StableStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DebateImportance(_StableStrEnum):
    CRITICAL = "critical"
    MATERIAL = "material"
    SECONDARY = "secondary"


class RiskReviewDisposition(_StableStrEnum):
    RETAINED = "retained"
    MODIFIED = "modified"
    REJECTED = "rejected"


class ResearchScenarioKind(_StableStrEnum):
    BASE = "base"
    BULL = "bull"
    BEAR = "bear"


class ScenarioReferenceCategory(_StableStrEnum):
    """Research purpose of a non-valuation scenario reference range."""

    TECHNICAL = "technical"
    HISTORICAL = "historical"
    ANALYST_CONSENSUS = "analyst_consensus"
    FUNDAMENTAL = "fundamental"
    OTHER = "other"


class NumericAuditComponentType(_StableStrEnum):
    """Stable component identity for localized numeric audit omissions."""

    APPENDIX = "appendix"
    CALCULATION = "calculation"
    SCENARIO_RANGE = "scenario_range"
    VALUATION = "valuation"
    MARKET_REFERENCE = "market_reference"
    DECISION_CLAIM = "decision_claim"


class NumericAuditStatus(_StableStrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class NumericAuditAppendixStatus(_StableStrEnum):
    COMPLETE = "complete"
    RECOVERED = "recovered"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"


class NumericCalculationStatus(_StableStrEnum):
    VERIFIED = "verified"
    INVALID = "invalid"
    MISSING = "missing"


class NumericDisplayStatus(_StableStrEnum):
    MATCHED = "matched"
    APPROXIMATELY_MATCHED = "approximately_matched"
    MISMATCHED = "mismatched"
    NOT_CHECKED = "not_checked"


class NumericDisplayScale(_StableStrEnum):
    """Deterministic scale applied only when comparing reader-facing values."""

    BASE = "base"
    THOUSAND = "thousand"
    TEN_THOUSAND = "ten_thousand"
    MILLION = "million"
    HUNDRED_MILLION = "hundred_million"
    BILLION = "billion"
    TRILLION = "trillion"


class NumericAuditPhase(_StableStrEnum):
    INITIAL = "initial"
    REPAIR = "repair"


class ArtifactGenerationMethod(_StableStrEnum):
    """Auditable method that produced a typed research artifact."""

    TOOL_CALL = "tool_call"
    TOOL_CALL_RECOVERED = "tool_call_recovered"
    JSON_MODE = "json_mode"
    RAW_JSON_RECOVERED = "raw_json_recovered"
    JSON_MODE_RECOVERED = "json_mode_recovered"
    SECTIONED_RECOVERY = "sectioned_recovery"
    MARKDOWN_AUDITED = "markdown_audited"
    MARKDOWN_AUDIT_INCOMPLETE = "markdown_audit_incomplete"


def _field_value(value: Any, field: str) -> Any:
    if isinstance(value, BaseModel):
        return getattr(value, field, None)
    if isinstance(value, dict):
        return value.get(field)
    return None


CURRENT_RESEARCH_SCHEMA_VERSION = "2"
