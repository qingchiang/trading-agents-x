"""Web-only request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.application.contracts import (
    AnalysisCutoffContext,
    AnalysisRequest,
    AnalysisResult,
    EvidenceSealView,
    FullBaselineCandidate,
    IncrementalRunContext,
    ResearchNodeComparisonSelection,
    ResearchNodeView,
    ResearchTimeline,
    RunAttemptView,
    RunLifecycleImpact,
    RunRequestSnapshot,
    RunStatus,
    RunView,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstrumentAdmissionErrorCode(StrEnum):
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
    ELIGIBILITY_UNAVAILABLE = "instrument_eligibility_unavailable"


class InstrumentAdmissionError(ApiModel):
    code: InstrumentAdmissionErrorCode
    message: str


class InstrumentAdmissionErrorResponse(ApiModel):
    error: InstrumentAdmissionError


class RequestValidationErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"


class RequestValidationError(ApiModel):
    code: RequestValidationErrorCode
    message: str


class RequestValidationDetail(ApiModel):
    location: list[str]
    message: str
    type: str


class RequestValidationErrorResponse(ApiModel):
    error: RequestValidationError
    details: list[RequestValidationDetail]


class AnalysisCutoffErrorCode(StrEnum):
    FUTURE_ANALYSIS_CUTOFF = "future_analysis_cutoff"


class AnalysisCutoffError(ApiModel):
    code: AnalysisCutoffErrorCode
    message: str


class AnalysisCutoffErrorResponse(ApiModel):
    error: AnalysisCutoffError
    requested_analysis_date: date
    context: AnalysisCutoffContext


class LoginRequest(ApiModel):
    token: str = Field(min_length=1, max_length=4096)


class RunDetail(ApiModel):
    run: RunView
    result: AnalysisResult | None = None
    research_node: ResearchNodeView | None = None
    attempts: tuple[RunAttemptView, ...] = ()
    evidence_status: EvidenceSealView
    incremental_context: IncrementalRunContext | None = None


class RunCreationTemplate(ApiModel):
    run_id: str
    status: RunStatus
    request: RunRequestSnapshot | AnalysisRequest
    research_kind: Literal["full", "incremental"] | None = None
    full_baseline_run_id: str | None = None
    instrument_name: str | None = None
    instrument_local_name: str | None = None


class FullBaselineCandidates(ApiModel):
    instrument: str
    before: date
    items: tuple[FullBaselineCandidate, ...] = ()


class TimelineDetail(ApiModel):
    timeline: ResearchTimeline


class ResearchNodeComparisonRequest(ApiModel):
    nodes: tuple[ResearchNodeComparisonSelection, ...] = Field(min_length=2, max_length=2)


class PrimaryCycleSelectionRequest(ApiModel):
    full_run_id: str = Field(min_length=1, max_length=36)

    @field_validator("full_run_id", mode="before")
    @classmethod
    def normalize_full_run_id(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class RunCreateRequest(AnalysisRequest):
    source_run_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("source_run_id", mode="before")
    @classmethod
    def normalize_source_run_id(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    def analysis_request(self) -> AnalysisRequest:
        return AnalysisRequest.model_validate(self.model_dump(exclude={"source_run_id"}))


class RunBatchRequest(ApiModel):
    run_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    primary_replacements: dict[str, str] = Field(default_factory=dict)

    @field_validator("run_ids")
    @classmethod
    def validate_run_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(run_id.strip() for run_id in value)
        if any(not run_id for run_id in normalized):
            raise ValueError("run IDs must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("run IDs must be unique")
        return normalized

    @field_validator("primary_replacements")
    @classmethod
    def validate_primary_replacements(cls, value: dict[str, str]) -> dict[str, str]:
        normalized = {key.strip(): replacement.strip() for key, replacement in value.items()}
        if any(not key or not replacement for key, replacement in normalized.items()):
            raise ValueError("Primary replacement IDs must not be empty")
        return normalized


class RunBatchResult(ApiModel):
    runs: tuple[RunView, ...]
    changed: int = Field(ge=0)
    impacts: tuple[RunLifecycleImpact, ...] = ()


class ExportQuery(ApiModel):
    format: Literal["package", "markdown", "json"] = "package"


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    queue: QueueHealth
    version: str


class QueueHealth(ApiModel):
    queued: int
    running: int


class ProviderCapabilities(ApiModel):
    label: str
    api_key_required: bool
    api_key_configured: bool | None
    configured: bool
    selectable: bool
    unavailable_reason: str | None = None
    model_discovery_supported: bool


class DiscoveredModelView(ApiModel):
    id: str
    label: str
    compatibility: Literal["supported", "unknown"]
    reasoning_efforts: list[str]
    default_roles: list[Literal["quick", "deep"]]


class ModelDiscoveryWarningView(ApiModel):
    code: str
    message: str


class ProviderModelCatalog(ApiModel):
    provider: str
    models: list[DiscoveredModelView]
    source: Literal["live", "cache", "fallback"]
    fetched_at: datetime
    stale: bool
    warning: ModelDiscoveryWarningView | None = None


class CapabilityDefaults(ApiModel):
    profile: str
    llm_provider: str
    quick_model: str
    deep_model: str
    quick_reasoning_effort: str | None
    deep_reasoning_effort: str | None
    output_language: str
    lan_enabled: bool
    trash_retention_days: int = Field(ge=0)


class CapabilitiesResponse(ApiModel):
    profiles: list[str]
    analysts: list[str]
    output_languages: list[str]
    providers: dict[str, ProviderCapabilities]
    defaults: CapabilityDefaults
