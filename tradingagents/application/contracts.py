"""Stable, typed contracts shared by Python, CLI, worker, and Web API clients."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from tradingagents.dataflows.symbol_utils import (
    crypto_base,
    market_timezone,
    normalize_symbol,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]*$")
_MEMORY_REF_PATTERN = re.compile(
    r"^memory:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)


def utc_now() -> datetime:
    """Return an aware UTC timestamp for public contracts."""
    return datetime.now(timezone.utc)


class FrozenModel(BaseModel):
    """Base class for immutable public value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RunProfile(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class ReportLanguage(str, Enum):
    ENGLISH = "en"
    SIMPLIFIED_CHINESE = "zh-CN"
    JAPANESE = "ja"

    @property
    def prompt_label(self) -> str:
        return {
            ReportLanguage.ENGLISH: "English (en)",
            ReportLanguage.SIMPLIFIED_CHINESE: (
                "Simplified Chinese (简体中文，中国大陆，zh-CN)"
            ),
            ReportLanguage.JAPANESE: "Japanese (日本語, ja)",
        }[self]


_REPORT_LANGUAGE_ALIASES = {
    "en": ReportLanguage.ENGLISH,
    "english": ReportLanguage.ENGLISH,
    "english (en)": ReportLanguage.ENGLISH,
    "zh-cn": ReportLanguage.SIMPLIFIED_CHINESE,
    "zh-hans": ReportLanguage.SIMPLIFIED_CHINESE,
    "chinese": ReportLanguage.SIMPLIFIED_CHINESE,
    "simplified chinese": ReportLanguage.SIMPLIFIED_CHINESE,
    "simplified chinese (简体中文, zh-hans)": (
        ReportLanguage.SIMPLIFIED_CHINESE
    ),
    "simplified chinese (简体中文，中国大陆，zh-cn)": (
        ReportLanguage.SIMPLIFIED_CHINESE
    ),
    "简体中文": ReportLanguage.SIMPLIFIED_CHINESE,
    "ja": ReportLanguage.JAPANESE,
    "japanese": ReportLanguage.JAPANESE,
    "japanese (日本語, ja)": ReportLanguage.JAPANESE,
    "日本語": ReportLanguage.JAPANESE,
}


def normalize_report_language(value: str | ReportLanguage) -> ReportLanguage:
    """Normalize public and legacy language spellings to one locale tag."""
    if isinstance(value, ReportLanguage):
        return value
    normalized = str(value).strip()
    language = _REPORT_LANGUAGE_ALIASES.get(normalized.casefold())
    if language is None:
        supported = ", ".join(language.value for language in ReportLanguage)
        raise ValueError(f"unsupported report language; expected one of {supported}")
    return language


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class ResearchRating(str, Enum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class EvidenceQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class EvidenceItem(FrozenModel):
    """One immutable, auditable evidence item in a run."""

    ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    source: str = Field(min_length=1, max_length=200)
    evidence_type: str = Field(min_length=1, max_length=120)
    requested_date: date
    effective_date: date | None = None
    available_at: datetime | None = None
    content: str | None = None
    value: float | int | str | None = None
    unit: str | None = None
    quality: EvidenceQuality = EvidenceQuality.MEDIUM
    fallback: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        source: str,
        evidence_type: str,
        requested_date: date,
        effective_date: date | None = None,
        available_at: datetime | None = None,
        content: str | None = None,
        value: float | int | str | None = None,
        unit: str | None = None,
        quality: EvidenceQuality = EvidenceQuality.MEDIUM,
        fallback: bool = False,
        provenance: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        payload = {
            "source": source,
            "evidence_type": evidence_type,
            "requested_date": requested_date.isoformat(),
            "effective_date": effective_date.isoformat() if effective_date else None,
            "available_at": available_at.isoformat() if available_at else None,
            "content": content,
            "value": value,
            "unit": unit,
            "provenance": provenance or {},
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return cls(
            ref=f"ev_{digest}",
            source=source,
            evidence_type=evidence_type,
            requested_date=requested_date,
            effective_date=effective_date,
            available_at=available_at,
            content=content,
            value=value,
            unit=unit,
            quality=quality,
            fallback=fallback,
            provenance=provenance or {},
        )


class EvidenceBundle(FrozenModel):
    """Versioned evidence snapshot shared by every agent in one run."""

    version: Literal["1"] = "1"
    instrument: str
    analysis_date: date
    items: tuple[EvidenceItem, ...]
    sealed_at: datetime = Field(default_factory=utc_now)
    digest: str | None = None

    @model_validator(mode="after")
    def validate_bundle(self) -> EvidenceBundle:
        refs = [item.ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("evidence refs must be unique")
        for item in self.items:
            if item.effective_date and item.effective_date > self.analysis_date:
                raise ValueError(
                    f"{item.ref} effective_date is after the analysis cutoff"
                )
            if item.available_at:
                if item.available_at.utcoffset() is None:
                    raise ValueError(
                        f"{item.ref} available_at must include a timezone"
                    )
                available_date = item.available_at.astimezone(
                    market_timezone(self.instrument)
                ).date()
                if available_date > self.analysis_date:
                    raise ValueError(
                        f"{item.ref} available_at is after the analysis cutoff"
                    )
        canonical = json.dumps(
            [item.model_dump(mode="json") for item in self.items],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        calculated = hashlib.sha256(canonical.encode()).hexdigest()
        if self.digest is None:
            object.__setattr__(self, "digest", calculated)
        elif self.digest != calculated:
            raise ValueError("evidence bundle digest does not match its items")
        return self


class AnalystClaim(FrozenModel):
    text: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class ResearchWarning(FrozenModel):
    """Structured, plain-text warning suitable for APIs and audit exports."""

    code: str = Field(default="legacy.warning", pattern=r"^[a-z0-9_.-]+$")
    message: str = Field(min_length=1, max_length=2000)
    evidence_ref: str | None = Field(
        default=None,
        pattern=r"^ev_[a-f0-9]{12}$",
    )
    source: str | None = Field(default=None, max_length=200)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> str:
        text = str(value)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"(\*\*|__|`)", "", text)
        return " ".join(text.split()).strip()


def _coerce_warnings(value: Any) -> tuple[ResearchWarning, ...]:
    if value is None:
        return ()
    items = (value,) if isinstance(value, (str, dict, ResearchWarning)) else value
    warnings = []
    for item in items:
        if isinstance(item, ResearchWarning):
            warning = item
        elif isinstance(item, str):
            warning = ResearchWarning(message=item)
        else:
            warning = ResearchWarning.model_validate(item)
        warnings.append(warning)
    return tuple(dict.fromkeys(warnings))


class AnalystReport(FrozenModel):
    """Typed analyst hand-off; narrative remains available for human readers."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    summary: str
    claims: tuple[AnalystClaim, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()
    narrative: str

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class PerspectiveReview(FrozenModel):
    role: str
    thesis: str
    claim_rebuttals: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    new_evidence_refs: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


class ResearchDecision(FrozenModel):
    """Research-only conclusion; deliberately excludes account-level advice."""

    rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    evidence_refs: tuple[str, ...] = ()
    memory_refs: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    time_horizon: str

    @field_validator("memory_refs")
    @classmethod
    def validate_memory_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not _MEMORY_REF_PATTERN.fullmatch(ref) for ref in refs):
            raise ValueError("memory refs must use the memory:<run_id> format")
        return refs


class MemoryOutcome(FrozenModel):
    """Completed five-or-more-interval feedback for one past decision."""

    benchmark: str
    observation_start: date | None = None
    observation_end: date | None = None
    holding_intervals: int = Field(ge=5)
    raw_return: float
    alpha_return: float


class MemoryRecord(FrozenModel):
    """One auditable memory item supplied to a research decision node."""

    ref: str
    run_id: str
    scope: Literal["same_ticker", "same_market"]
    ticker: str
    market: str | None = None
    analysis_date: date
    decision: ResearchDecision | None = None
    outcome: MemoryOutcome | None = None
    reflection: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_record(self) -> MemoryRecord:
        if not _MEMORY_REF_PATTERN.fullmatch(self.ref):
            raise ValueError("memory ref must use the memory:<run_id> format")
        if self.ref != f"memory:{self.run_id}":
            raise ValueError("memory ref must identify its run_id")
        if self.scope == "same_ticker":
            if self.decision is None or self.outcome is None:
                raise ValueError(
                    "same-ticker memory requires decision and outcome"
                )
        elif self.decision is not None or self.outcome is not None:
            raise ValueError(
                "same-market memory must contain reflection-only feedback"
            )
        return self

    def prompt_text(self, max_chars: int = 2000) -> str:
        """Render one bounded block without turning memory into evidence."""
        parts = [
            f"REF: {self.ref}",
            f"SCOPE: {self.scope}",
            (
                f"PAST RUN: {self.analysis_date} | {self.ticker} | "
                f"{self.market or 'unknown market'}"
            ),
        ]
        if self.decision is not None:
            parts.append(
                "PAST DECISION:\n"
                + json.dumps(
                    self.decision.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        if self.outcome is not None:
            parts.append(
                "OBSERVED OUTCOME:\n"
                + json.dumps(
                    self.outcome.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        parts.append(f"REFLECTION:\n{self.reflection}")
        rendered = "\n".join(parts)
        if max_chars <= 0:
            return ""
        if len(rendered) <= max_chars:
            return rendered
        if max_chars == 1:
            return "…"
        return rendered[: max_chars - 1] + "…"


class MemoryContext(FrozenModel):
    """Deterministic, bounded historical feedback for one current run."""

    version: Literal["1"] = "1"
    instrument: str
    market: str | None = None
    items: tuple[MemoryRecord, ...] = ()

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.items)

    def prompt_text(
        self,
        *,
        max_chars: int = 12_000,
        item_max_chars: int = 2_000,
    ) -> str:
        if not self.items or max_chars <= 0 or item_max_chars <= 0:
            return ""
        separators = 2 * (len(self.items) - 1)
        available = max(0, max_chars - separators)
        per_item = min(item_max_chars, available // len(self.items))
        if per_item <= 0:
            return ""
        return "\n\n".join(
            item.prompt_text(per_item) for item in self.items
        )[:max_chars]


ResearchArtifactContent = AnalystReport | PerspectiveReview | ResearchDecision


def _artifact_content_type(content: ResearchArtifactContent) -> str:
    if isinstance(content, AnalystReport):
        return "analyst_report"
    if isinstance(content, PerspectiveReview):
        return "perspective_review"
    return "research_decision"


class ResearchArtifactDraft(FrozenModel):
    """Typed graph output awaiting application-owned persistence metadata."""

    node: str = Field(min_length=1, max_length=160)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["1"] = "1"
    content: ResearchArtifactContent

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.content.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ResearchArtifact(FrozenModel):
    """Durable, typed output from one visible research stage."""

    id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    attempt: int = Field(ge=1)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["1"] = "1"
    content: ResearchArtifactContent
    created_at: datetime

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)


class RunMetrics(FrozenModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    node_wall_times: dict[str, float] = Field(default_factory=dict)


class AnalysisRequest(FrozenModel):
    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    asset_type: AssetType | None = None
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[
        Literal["market", "social", "news", "fundamentals"], ...
    ] = ("market", "social", "news", "fundamentals")
    llm_provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    output_language: ReportLanguage | None = None
    provenance: bool | None = None

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
        value: str | ReportLanguage | None,
    ) -> ReportLanguage | None:
        if value is None:
            return None
        return normalize_report_language(value)

    @model_validator(mode="after")
    def infer_asset_type(self) -> AnalysisRequest:
        inferred = AssetType.CRYPTO if crypto_base(self.ticker) else AssetType.STOCK
        if self.asset_type is None:
            object.__setattr__(self, "asset_type", inferred)
        elif self.asset_type != inferred and inferred is AssetType.CRYPTO:
            raise ValueError("known crypto symbols must use asset_type='crypto'")
        if inferred is AssetType.CRYPTO and "fundamentals" in self.analysts:
            compatible = tuple(
                analyst for analyst in self.analysts if analyst != "fundamentals"
            )
            if not compatible:
                raise ValueError(
                    "crypto analysis requires a non-fundamentals analyst"
                )
            object.__setattr__(self, "analysts", compatible)
        return self


class RunEvent(FrozenModel):
    run_id: str
    sequence: int = Field(ge=1)
    attempt: int = Field(ge=1)
    event_type: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AnalysisResult(FrozenModel):
    run_id: str
    status: RunStatus
    instrument: str
    reports: dict[str, AnalystReport | str]
    decision: ResearchDecision | None
    evidence: EvidenceBundle | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class RunView(FrozenModel):
    id: str
    parent_run_id: str | None = None
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
    updated_at: datetime


class RunExport(FrozenModel):
    """Versioned, self-contained durable run export."""

    schema_version: Literal["1"] = "1"
    run: RunView
    result: AnalysisResult
    evidence: EvidenceBundle | None = None
    artifacts: tuple[ResearchArtifact, ...] = ()
