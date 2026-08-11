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
from tradingagents.application.outcome_feedback import (
    OutcomeFeedbackStatus,
    OutcomeObservationStatus,
    OutcomeReflectionStatus,
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


class OutcomeFeedbackRetireRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


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


class OutcomeObservationView(ApiModel):
    status: OutcomeObservationStatus
    source_decision_id: int
    source_revision_id: str | None
    benchmark: str
    market_timezone: str
    method_category: str
    method_version: str
    price_semantics: str
    adjustment_semantics: str
    horizon_limit: str
    limitations: list[str]
    observation_start: str | None = Field(
        description=(
            "Market-local return-baseline date; it may equal but cannot precede "
            "the effective source cutoff."
        )
    )
    observation_end: str | None = Field(
        description="Market-local observation end, strictly after the effective source cutoff."
    )
    holding_intervals: int
    raw_return: float | None
    alpha_return: float | None
    resolved_at: datetime | None
    data_available_at: datetime | None
    last_checked_at: datetime | None
    next_check_at: datetime | None
    error_message: str | None


class OutcomeReflectionView(ApiModel):
    status: OutcomeReflectionStatus
    created_at: datetime
    generated_at: datetime | None
    last_attempted_at: datetime | None
    next_retry_at: datetime | None
    error_code: str | None
    generation_cycle: ReflectionGenerationCycleView | None = None


class ReflectionGenerationCycleView(ApiModel):
    id: str
    outcome_id: int
    status: Literal["queued", "running", "succeeded", "failed", "invalid"]
    origin: Literal["automatic", "manual", "legacy"]
    trigger: str
    retry_ordinal: int
    queued_at: datetime
    due_at: datetime | None


class ReflectionRegenerationAccepted(ApiModel):
    cycle: ReflectionGenerationCycleView


class ReflectionAttemptUsageView(ApiModel):
    usage_status: Literal["reported", "not_reported", "legacy_unknown"]
    llm_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_hit_input_tokens: int | None
    cache_miss_input_tokens: int | None
    reasoning_output_tokens: int | None
    wall_time_seconds: float | None
    provider_reported_cost_usd: float | None


class ReflectionAttemptView(ApiModel):
    id: int
    generation_cycle_id: str
    sequence: int
    trigger: str
    origin: str
    attempt_kind: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str | None
    schema_version: str | None
    diagnostics: dict[str, str] | None
    usage: ReflectionAttemptUsageView
    invalid_candidate: str | None
    invalid_candidate_digest: str | None
    invalid_candidate_length: int | None
    validation_issues: list[str] | None


class ReflectionUsageAggregateView(ReflectionAttemptUsageView):
    attempt_count: int


class OutcomeFeedbackApplicabilityView(ApiModel):
    schema_version: Literal["1"]
    scope: Literal["instrument", "market"]
    instrument: str | None
    market: str | None
    research_stages: list[str]
    research_domains: list[str]
    method_category: str
    horizon: str


class OutcomeFeedbackView(ApiModel):
    id: int
    status: OutcomeFeedbackStatus
    qualification_policy_version: str | None = Field(
        description=(
            "Prospective qualification-policy version, independent from the Feedback "
            "schema and Observation method versions; legacy rows may be unversioned."
        )
    )
    reasons: list[str]
    method_category: str
    horizon_limit: str
    applicability: OutcomeFeedbackApplicabilityView
    qualified_at: datetime
    available_at: datetime = Field(
        description=(
            "Latest availability time of the Observation data, Reflection, and qualification."
        )
    )
    retired_at: datetime | None


class ResearchReview(ApiModel):
    outcome_id: int
    review_status: Literal[
        "awaiting_observation",
        "observation_delayed",
        "awaiting_reflection",
        "reflection_retry_scheduled",
        "reflection_failed",
        "reflection_invalid",
        "feedback_available",
        "feedback_ineligible",
        "feedback_retired",
        "lifecycle_inconsistent",
    ]
    lifecycle_actions_allowed: bool
    run_id: str
    ticker: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    market: str | None
    asset_type: str
    analysis_date: str
    profile: RunProfile
    decision: ResearchDecision
    outcome: OutcomeObservationView
    method_feedback: str | None
    outcome_reflection: OutcomeReflectionView | None
    outcome_feedback: OutcomeFeedbackView | None


class ResearchReviewAuditDetail(ApiModel):
    review: ResearchReview
    reflection: str | None
    attempts: list[ReflectionAttemptView]
    aggregate_usage: ReflectionUsageAggregateView
