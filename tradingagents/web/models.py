"""Web-only request and response schemas."""

from __future__ import annotations

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


class ModelOption(ApiModel):
    label: str
    value: str


class ProviderCapabilities(ApiModel):
    quick_models: list[ModelOption]
    deep_models: list[ModelOption]
    reasoning_efforts: dict[str, list[str]]
    api_key_configured: bool | None


class CapabilityDefaults(ApiModel):
    profile: str
    llm_provider: str
    quick_model: str
    deep_model: str
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
