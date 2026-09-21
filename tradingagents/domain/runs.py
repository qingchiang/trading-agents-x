"""Runs contracts."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from tradingagents.domain.common import (
    _SYMBOL_PATTERN,
    ArtifactGenerationMethod,
    FrozenModel,
    OutputLanguage,
    ReportLanguage,
    RunProfile,
    RunStatus,
    normalize_report_language,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.instruments import (
    is_supported_equity_symbol,
    normalize_symbol,
    unsupported_crypto_base,
)
from tradingagents.domain.model_selection import RoleSelections
from tradingagents.domain.numeric_audit import DecisionNumericAuditAppendix
from tradingagents.domain.reporting import order_reports
from tradingagents.domain.reports import AnalystReport, ResearchWarning, _coerce_warnings


class NodeMetrics(FrozenModel):
    """Resource usage attributed to one research graph node."""

    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_hit_input_tokens: int = Field(default=0, ge=0)
    cache_miss_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    detailed_usage_calls: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)


class RunMetrics(FrozenModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_hit_input_tokens: int = Field(default=0, ge=0)
    cache_miss_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    detailed_usage_calls: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    node_metrics: dict[str, NodeMetrics] = Field(default_factory=dict)


class AnalysisRequest(FrozenModel):
    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[Literal["market", "social", "news", "fundamentals"], ...] = (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    models: RoleSelections = Field(default_factory=RoleSelections)
    research_kind: Literal["full", "incremental"] = "full"
    full_baseline_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    # The first Full Cycle is selected automatically.  Once a Timeline exists,
    # callers must make this choice explicitly rather than relying on order.
    make_primary: bool | None = None
    # Keep the union inline so Pydantic preserves the existing OpenAPI shape;
    # a named PEP 695 alias is emitted as a separate schema component.
    output_language: ReportLanguage | str | None = None

    @field_validator("models", mode="before")
    @classmethod
    def normalize_models(cls, value):
        return {} if value is None else value

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        canonical = normalize_symbol(value)
        if not canonical:
            raise ValueError("ticker must not be empty")
        if not _SYMBOL_PATTERN.fullmatch(canonical):
            raise ValueError("ticker contains unsupported characters")
        return canonical

    @field_validator("analysts")
    @classmethod
    def validate_analysts(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one analyst must be selected")
        if len(value) != len(set(value)):
            raise ValueError("analysts must not contain duplicates")
        order = ("market", "social", "news", "fundamentals")
        return tuple(key for key in order if key in value)

    @field_validator("output_language", mode="before")
    @classmethod
    def normalize_output_language(
        cls,
        value: OutputLanguage | None,
    ) -> OutputLanguage | None:
        if value is None:
            return None
        return normalize_report_language(value)

    @model_validator(mode="after")
    def validate_research_request(self) -> AnalysisRequest:
        if unsupported_crypto_base(self.ticker):
            raise ValueError("Crypto instruments are not supported")
        if not is_supported_equity_symbol(self.ticker):
            raise ValueError("Only listed equity instruments are supported")
        if self.research_kind == "full" and self.full_baseline_run_id is not None:
            raise ValueError("Full Research must not carry a Full Baseline")
        if self.research_kind == "incremental" and self.full_baseline_run_id is None:
            raise ValueError("Incremental Research requires exactly one full_baseline_run_id")
        if self.research_kind == "incremental" and self.models.quick is not None:
            raise ValueError("Incremental Research must not override the quick model role")
        return self


class AnalysisCutoffContext(FrozenModel):
    """Server-observed market-local date boundary for a Listed Instrument."""

    instrument: str
    market_timezone: str
    market_date: date
    max_analysis_date: date
    observed_at: datetime
    valid_until: datetime


class RunRequestSnapshot(FrozenModel):
    """Tolerant request data retained with a Run for history inspection.

    This is deliberately separate from :class:`AnalysisRequest`.  The latter
    is the admission contract for creating research, while this snapshot must
    remain able to represent request values that were accepted by an older
    application version (including ``asset_type='crypto'``).  Snapshot
    validation does not normalize symbols, infer an asset type, or otherwise
    rewrite persisted request data.
    """

    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[Literal["market", "social", "news", "fundamentals"], ...] = (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    models: RoleSelections = Field(default_factory=RoleSelections)
    research_kind: Literal["full", "incremental"] = "full"
    full_baseline_run_id: str | None = None
    make_primary: bool | None = None
    output_language: ReportLanguage | str | None = None

    def to_analysis_request(self) -> AnalysisRequest:
        """Cross the creation boundary explicitly when execution is requested."""

        return AnalysisRequest.model_validate(self.model_dump(mode="python"))


class RunEvent(FrozenModel):
    run_id: str
    sequence: int = Field(ge=1)
    attempt: int = Field(ge=1)
    event_type: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class StructuredRecoveryNotice(FrozenModel):
    """One successful bounded structured-output recovery rebuilt from events."""

    attempt: int = Field(ge=1)
    node: str = Field(min_length=1, max_length=160)
    initial_reason_code: str = Field(pattern=r"^[a-z0-9_.-]+$")
    recovery_method: ArtifactGenerationMethod
    validation_issue_codes: tuple[str, ...] = ()
    retry_count: int = Field(ge=1)
    recovered_at: datetime

    @field_validator("validation_issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("recovery issues must use stable codes")
        return issues


class AnalysisResult(FrozenModel):
    run_id: str
    status: RunStatus
    instrument: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    reports: dict[str, AnalystReport | str]
    decision: ResearchDecision | None
    numeric_audit: DecisionNumericAuditAppendix | None = None
    evidence: EvidenceBundle | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    recoveries: tuple[StructuredRecoveryNotice, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("reports")
    @classmethod
    def order_public_reports(
        cls,
        value: dict[str, AnalystReport | str],
    ) -> dict[str, AnalystReport | str]:
        return order_reports(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class EvidenceSealView(FrozenModel):
    """Durable status of the immutable evidence boundary for one run."""

    status: Literal["pending", "sealed"]
    digest: str | None = None
    item_count: int = Field(default=0, ge=0)
    table_count: int = Field(default=0, ge=0)
    sealed_attempt: int | None = Field(default=None, ge=1)
    sealed_at: datetime | None = None


class RunView(FrozenModel):
    id: str
    source_run_id: str | None = None
    is_research_node: bool = False
    research_schema_version: str | None = None
    information_cutoff_at: datetime | None = None
    method_snapshot: dict[str, Any] | None = None
    research_kind: Literal["full", "incremental"] | None = None
    full_baseline_run_id: str | None = None
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    status: RunStatus
    # Keep the creation schema referenced in OpenAPI for existing clients,
    # while repository hydration and all normal responses use the tolerant
    # snapshot branch below.
    request: RunRequestSnapshot | AnalysisRequest
    config_snapshot: dict[str, Any]
    audit_snapshot: dict[str, Any] | None = None
    attempt: int
    cancel_requested: bool
    error_code: str | None = None
    error_message: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trashed_at: datetime | None = None
    updated_at: datetime

    @field_validator("request", mode="before")
    @classmethod
    def coerce_creation_request(
        cls,
        value: RunRequestSnapshot | AnalysisRequest | Any,
    ) -> RunRequestSnapshot | AnalysisRequest | Any:
        if isinstance(value, AnalysisRequest):
            return value.model_dump(mode="python")
        return value
