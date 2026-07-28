"""Web-only request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.application.contracts import (
    AnalysisResult,
    ResearchDecision,
    RunView,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(ApiModel):
    token: str = Field(min_length=1, max_length=4096)


class RunDetail(ApiModel):
    run: RunView
    result: AnalysisResult | None = None


class ExportQuery(ApiModel):
    format: Literal["markdown", "json"] = "markdown"


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
    market: str | None
    asset_type: str
    analysis_date: str
    decision: ResearchDecision
    outcome: MemoryOutcome
    reflection: str | None
