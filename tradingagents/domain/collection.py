"""Collection contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from tradingagents.domain.common import (
    FrozenModel,
    _deeply_frozen_mapping,
    _StableStrEnum,
    _unique_evidence_refs,
)
from tradingagents.domain.evidence import EvidenceItem


class CollectionDiagnostic(FrozenModel):
    """A stable, secret-free collection failure class safe for Run events."""

    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")


class CollectionResultState(_StableStrEnum):
    """Truthful result state for one enabled Incremental research domain."""

    DATA = "data"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CollectionTemporalBasis(_StableStrEnum):
    """Temporal basis actually represented by admitted domain observations."""

    PIT = "pit"
    NEAR_LIVE_ADVISORY = "near_live_advisory"


class CollectionSourceProvenance(FrozenModel):
    """One actual source used or attempted for a domain result."""

    source: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    fallback: bool = False
    retrieved_at: datetime
    diagnostic: CollectionDiagnostic | None = None

    @model_validator(mode="after")
    def validate_retrieval_time(self) -> CollectionSourceProvenance:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("source retrieved_at must include a timezone")
        return self


class CollectionDomainResult(FrozenModel):
    """One actual domain result without a configured-provider attempt ledger."""

    domain: Literal["fundamentals", "market", "news", "social"]
    state: CollectionResultState
    sources: tuple[CollectionSourceProvenance, ...] = ()
    observed_from: datetime | None = None
    observed_through: datetime | None = None
    temporal_bases: tuple[CollectionTemporalBasis, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    diagnostic: CollectionDiagnostic | None = None
    omitted_by_temporal_boundary: bool = False

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_truthful_result(self) -> CollectionDomainResult:
        for field_name in ("observed_from", "observed_through"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must include a timezone")
        observed = (self.observed_from, self.observed_through)
        if any(value is not None for value in observed) and any(
            value is None for value in observed
        ):
            raise ValueError("observed window must be complete")
        if (
            self.observed_from is not None
            and self.observed_through is not None
            and self.observed_from > self.observed_through
        ):
            raise ValueError("observed window must be ordered")
        if self.state in {CollectionResultState.DATA, CollectionResultState.PARTIAL}:
            if not self.evidence_refs or not self.temporal_bases:
                raise ValueError("data and partial results require Evidence and a temporal basis")
        elif self.evidence_refs or self.temporal_bases:
            raise ValueError(
                "empty and unavailable results cannot report Evidence or a temporal basis"
            )
        if self.state is CollectionResultState.UNAVAILABLE:
            if self.diagnostic is None:
                raise ValueError("unavailable results require a sanitized diagnostic")
        elif self.state is CollectionResultState.PARTIAL and self.diagnostic is None:
            raise ValueError("partial results require a sanitized limitation")
        elif not self.sources:
            raise ValueError("queried collection results require actual sources")
        source_names = tuple(source.source for source in self.sources)
        if len(source_names) != len(set(source_names)):
            raise ValueError("collection result sources must be unique")
        if len(self.temporal_bases) != len(set(self.temporal_bases)):
            raise ValueError("collection temporal bases must be unique")
        return self


class CollectionSummary(FrozenModel):
    """Actual Incremental collection results and disclosed limitations."""

    version: str = Field(pattern=r"^[0-9]+$")
    market: Literal["united_states", "japan", "mainland_china"]
    domains: tuple[CollectionDomainResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_domains(self) -> CollectionSummary:
        domains = tuple(item.domain for item in self.domains)
        if len(domains) != len(set(domains)):
            raise ValueError("collection summary domains must be unique")
        return self


class ResearchAvailabilityStatus(_StableStrEnum):
    AVAILABLE = "available"
    LIMITED = "limited"
    MISSING = "missing"


class ResearchAvailabilityDomain(FrozenModel):
    domain: Literal["fundamentals", "market", "news", "social"]
    status: ResearchAvailabilityStatus


class ResearchAvailability(FrozenModel):
    """Descriptive breadth of actual inputs, without source certification."""

    version: str = Field(pattern=r"^[0-9]+$")
    domains: tuple[ResearchAvailabilityDomain, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_domains(self) -> ResearchAvailability:
        domains = tuple(item.domain for item in self.domains)
        if len(domains) != len(set(domains)):
            raise ValueError("research availability domains must be unique")
        return self


class IncrementalCollectionRequest(FrozenModel):
    """Frozen common request passed to one configured market collection seam."""

    version: str = Field(pattern=r"^[0-9]+$")
    instrument: str = Field(min_length=1)
    market: Literal["united_states", "japan", "mainland_china"]
    route_suffix: str
    baseline_analysis_cutoff: date
    analysis_cutoff: date
    window_start: datetime
    window_end: datetime
    enabled_domains: tuple[Literal["fundamentals", "market", "news", "social"], ...] = Field(
        min_length=1
    )
    configured_routes: Mapping[str, Any]
    near_live_max_age_days: int = Field(default=5, ge=0, le=5)

    @field_validator("configured_routes", mode="after")
    @classmethod
    def freeze_configured_routes(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return _deeply_frozen_mapping(value)

    @model_validator(mode="after")
    def validate_collection_boundary(self) -> IncrementalCollectionRequest:
        if self.baseline_analysis_cutoff >= self.analysis_cutoff:
            raise ValueError("Incremental analysis cutoff must follow the Full Baseline")
        if self.window_start >= self.window_end:
            raise ValueError("Incremental collection window must advance")
        for field_name in ("window_start", "window_end"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if len(self.enabled_domains) != len(set(self.enabled_domains)):
            raise ValueError("enabled collection domains must be unique")
        return self


class IncrementalEvidenceCandidate(FrozenModel):
    """One collector-produced record before its PIT availability is admitted.

    A source may establish a precise publication instant or only a market-local
    publication date.  The latter is deliberately resolved by the application
    at conservative day-end before it reaches a sealed EvidenceBundle.
    """

    evidence: EvidenceItem
    available_on: date | None = None

    @model_validator(mode="after")
    def validate_availability_shape(self) -> IncrementalEvidenceCandidate:
        if self.evidence.available_at is not None and self.available_on is not None:
            raise ValueError("Evidence availability must use either an instant or a date")
        return self


class IncrementalEvidenceBinding(FrozenModel):
    """Resolution from a collector-owned ref to the admitted sealed ref."""

    candidate_ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    admitted_ref: str | None = Field(default=None, pattern=r"^ev_[a-f0-9]{12}$")


class InformationAdvancement(FrozenModel):
    """Deterministic answer to whether collection can justify an Incremental Node."""

    advanced: bool
    reasons: tuple[
        Literal[
            "admissible_observation",
            "completed_stock_session",
        ],
        ...,
    ] = ()
    observation_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_advancement_inputs(self) -> InformationAdvancement:
        if self.advanced != bool(self.reasons):
            raise ValueError("information advancement must agree with its reasons")
        has_observations = bool(self.observation_ids)
        if has_observations != ("admissible_observation" in self.reasons):
            raise ValueError("new observation identities must agree with advancement reasons")
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("new observation identities must be unique")
        return self


class IncrementalCollectionPreflight(FrozenModel):
    """Simplified deterministic gate result, safe to persist in sanitized events."""

    collection_summary: CollectionSummary
    research_availability: ResearchAvailability
    information_advancement: InformationAdvancement
    diagnostics: tuple[CollectionDiagnostic, ...] = ()
