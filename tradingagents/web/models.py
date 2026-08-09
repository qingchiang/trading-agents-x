"""Web-only request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceSealView,
    ResearchDecision,
    RunAttemptView,
    RunProfile,
    RunView,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(ApiModel):
    token: str = Field(min_length=1, max_length=4096)


class RunDetail(ApiModel):
    run: RunView
    result: AnalysisResult | None = None
    attempts: tuple[RunAttemptView, ...] = ()
    evidence_status: EvidenceSealView


class RunCreateRequest(AnalysisRequest):
    source_run_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("source_run_id", mode="before")
    @classmethod
    def normalize_source_run_id(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    def analysis_request(self) -> AnalysisRequest:
        return AnalysisRequest.model_validate(self.model_dump(exclude={"source_run_id"}))


class ResearchChainUpdateRequest(ApiModel):
    baseline_revision_id: str = Field(min_length=1, max_length=36)
    analysis_date: date
    execution_strategy: Literal["full", "incremental"] | None = None


class RunBatchRequest(ApiModel):
    run_ids: tuple[str, ...] = Field(min_length=1, max_length=100)

    @field_validator("run_ids")
    @classmethod
    def validate_run_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(run_id.strip() for run_id in value)
        if any(not run_id for run_id in normalized):
            raise ValueError("run IDs must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("run IDs must be unique")
        return normalized


class RunBatchResult(ApiModel):
    runs: tuple[RunView, ...]
    changed: int = Field(ge=0)


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
    pending_outcomes: int


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


class MemoryOutcome(ApiModel):
    status: Literal["pending", "resolved"]
    benchmark: str
    observation_start: str | None
    observation_end: str | None
    holding_intervals: int
    raw_return: float | None
    alpha_return: float | None


class MemoryEntry(ApiModel):
    run_id: str
    ticker: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    market: str | None
    asset_type: str
    analysis_date: str
    profile: RunProfile
    decision: ResearchDecision
    outcome: MemoryOutcome
    reflection: str | None
