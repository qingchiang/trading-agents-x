"""Run request, lifecycle, result, and API-view contracts."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from tradingagents.application.reporting import order_reports
from tradingagents.dataflows.symbol_utils import (
    is_supported_equity_symbol,
    normalize_symbol,
    unsupported_crypto_base,
)

from ._contract_audit import ResearchUpdateAudit, RunMetrics
from ._contract_base import (
    _SYMBOL_PATTERN,
    ArtifactGenerationMethod,
    AssetType,
    FrozenModel,
    OutputLanguage,
    ReportLanguage,
    ResearchRating,
    RunProfile,
    RunStatus,
    normalize_report_language,
)
from ._contract_decision import (
    AnalystReport,
    ResearchArtifact,
    ResearchDecision,
    ResearchWarning,
    _coerce_warnings,
)
from ._contract_evidence import EvidenceBundle
from ._contract_numeric import DecisionNumericAuditAppendix


class AnalysisRequest(FrozenModel):
    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    asset_type: AssetType | None = None
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[Literal["market", "social", "news", "fundamentals"], ...] = (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    llm_provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    # Keep the union inline so Pydantic preserves the existing OpenAPI shape;
    # a named PEP 695 alias is emitted as a separate schema component.
    output_language: ReportLanguage | str | None = None
    anchor_readiness: Literal["required", "allow_non_anchor"] = Field(
        default="required",
        description=(
            "Require deterministic Forward Research Anchor readiness, or explicitly "
            "allow a Full-only non-anchor Research Chain."
        ),
    )

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
    def validate_asset_type(self) -> AnalysisRequest:
        if unsupported_crypto_base(self.ticker):
            raise ValueError("Crypto instruments are not supported")
        if not is_supported_equity_symbol(self.ticker):
            raise ValueError("Only listed equity instruments are supported")
        if self.asset_type is None:
            object.__setattr__(self, "asset_type", AssetType.STOCK)
        return self


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
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    research_chain_requested: bool = False
    update_intent_id: str | None = None
    research_chain_id: str | None = None
    baseline_revision_id: str | None = None
    research_execution_strategy: Literal["full", "incremental"] | None = None
    research_update_audit: ResearchUpdateAudit | None = None
    information_frontier: datetime | None = None
    status: RunStatus
    request: AnalysisRequest
    config_snapshot: dict[str, Any]
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


class RunAttemptView(FrozenModel):
    """Observed execution usage and lifecycle for one retry attempt."""

    attempt: int = Field(ge=1)
    status: RunStatus
    resume_count: int = Field(default=0, ge=0)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None


class RunSummaryView(RunView):
    research_rating: ResearchRating | None = None


class RunPage(FrozenModel):
    items: tuple[RunSummaryView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class RecentInstrument(FrozenModel):
    ticker: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    last_used_at: datetime


class RunExport(FrozenModel):
    """Versioned, self-contained durable run export."""

    schema_version: Literal["9"] = "9"
    run: RunView
    result: AnalysisResult
    evidence: EvidenceBundle | None = None
    artifacts: tuple[ResearchArtifact, ...] = ()
    attempts: tuple[RunAttemptView, ...] = ()
