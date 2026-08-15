"""Deterministic preflight for establishing a Japanese Forward Research Anchor."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.provenance import (
    CoverageLimitationKind,
    SourceWatermark,
    extract_source_observations_strict,
    extract_source_watermarks,
)

from .contracts import AnalysisRequest, NodeMetrics, RunMetrics
from .market_readiness import MarketDataReadiness
from .research import (
    JAPANESE_ANCHOR_PROFILE,
    CapabilityAttestation,
    CapabilitySourceContract,
    MarketResearchCapability,
)

_JQUANTS_MARKET_SOURCE = "J-Quants adjusted OHLCV"


class AnchorReadinessReason(StrEnum):
    """Stable fail-closed outcomes shared by service and validation tooling."""

    MISSING_MARKET_OBSERVATION = "missing_market_observation"
    REQUIRED_CAPABILITY_UNAVAILABLE = "required_capability_unavailable"
    UNSAFE_POINT_IN_TIME_BOUNDARY = "unsafe_point_in_time_boundary"
    INVALID_SOURCE_CLOSURE = "invalid_source_closure"


class AnchorReadinessSourceFrontier(BaseModel):
    """Sanitized source boundary retained by a readiness result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    capability: MarketResearchCapability
    status: Literal["complete", "limited", "unavailable"]
    information_frontier: datetime
    observed_start: date
    observed_end: date
    requested_start: date
    requested_end: date
    limitations: tuple[str, ...] = ()
    limitation_kind: CoverageLimitationKind | None = None
    returned_records: int | None = Field(default=None, ge=0)
    reported_records: int | None = Field(default=None, ge=0)
    record_versions_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_record_closure(self) -> AnchorReadinessSourceFrontier:
        if self.returned_records is None:
            if self.reported_records is not None or self.record_versions_digest is not None:
                raise ValueError("record closure fields require returned_records")
            return self
        if self.record_versions_digest is None:
            raise ValueError("record closure requires a version digest")
        if (
            self.reported_records is not None
            and self.reported_records < self.returned_records
        ):
            raise ValueError("reported_records must cover returned_records")
        return self


class AnchorReadinessResult(BaseModel):
    """Zero-LLM capability result safe for events and sanitized manifests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    ready: bool
    requested_cutoff: date
    information_frontier: datetime
    profile_id: str
    reasons: tuple[AnchorReadinessReason, ...] = ()
    capabilities: tuple[CapabilityAttestation, ...] = ()
    source_frontiers: tuple[AnchorReadinessSourceFrontier, ...] = ()
    limitations: tuple[str, ...] = ()
    metrics: RunMetrics


class AnchorReadinessError(RuntimeError):
    """Refuse an anchor-required execution while retaining its typed result."""

    def __init__(self, result: AnchorReadinessResult):
        self.result = result
        reason = result.reasons[0].value if result.reasons else "not_ready"
        super().__init__(f"Forward Research Anchor readiness failed: {reason}")


def source_record_versions_digest(version_ids: Iterable[str]) -> str:
    """Return a deterministic closure digest without exposing record identities."""

    canonical = "\n".join(sorted(version_ids))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _metrics(started: float, tool_calls: int) -> RunMetrics:
    elapsed = max(0.0, monotonic() - started)
    node = NodeMetrics(tool_calls=tool_calls, wall_time_seconds=elapsed)
    return RunMetrics(
        tool_calls=tool_calls,
        wall_time_seconds=elapsed,
        node_metrics={"research.anchor_readiness": node},
    )


def _result(
    request: AnalysisRequest,
    frontier: datetime,
    started: float,
    tool_calls: int,
    *,
    reasons: tuple[AnchorReadinessReason, ...],
    capabilities: tuple[CapabilityAttestation, ...] = (),
    source_frontiers: tuple[AnchorReadinessSourceFrontier, ...] = (),
    limitations: tuple[str, ...] = (),
) -> AnchorReadinessResult:
    return AnchorReadinessResult(
        ready=not reasons,
        requested_cutoff=request.analysis_date,
        information_frontier=frontier,
        profile_id=JAPANESE_ANCHOR_PROFILE.id,
        reasons=tuple(dict.fromkeys(reasons)),
        capabilities=capabilities,
        source_frontiers=source_frontiers,
        limitations=limitations,
        metrics=_metrics(started, tool_calls),
    )


def _usable_source_watermark(
    source: str,
    *,
    watermarks: tuple[SourceWatermark, ...],
    information_frontier: datetime,
    requested_cutoff: date,
    transition_start: date | None,
) -> SourceWatermark | None:
    candidates = tuple(item for item in watermarks if item.source == source)
    return next(
        (
            item
            for item in candidates
            if item.status in {"complete", "limited"}
            and item.scanned_end == requested_cutoff.isoformat()
            and item.temporal_scope == "point_in_time"
            and item.information_frontier is not None
            and datetime.fromisoformat(item.information_frontier) == information_frontier
            and (
                transition_start is None
                or date.fromisoformat(item.scanned_start) <= transition_start
            )
            and (
                not item.limitations
                or (
                    item.limitation_kind == "archive_truncation"
                    and all(
                        "rolling archive" in limitation.lower() for limitation in item.limitations
                    )
                )
            )
        ),
        None,
    )


def _selected_source_set(
    contract: CapabilitySourceContract,
    usable_by_source: dict[str, SourceWatermark | None],
) -> tuple[str, ...] | None:
    """Return the first complete alternative; each member is complementary."""
    return next(
        (
            source_set
            for source_set in contract.acceptable_source_sets
            if all(usable_by_source.get(source) is not None for source in source_set)
        ),
        None,
    )


def validate_japanese_anchor_readiness(
    request: AnalysisRequest,
    *,
    information_frontier: datetime,
    market_checker: Callable[[str, date], MarketDataReadiness | None],
    news_collector: Callable[..., str],
    anchor_frontier: datetime | None = None,
) -> AnchorReadinessResult:
    """Check the Japanese minimum capability profile without constructing an LLM."""
    if information_frontier.utcoffset() is None:
        raise ValueError("Anchor readiness Information Frontier requires a timezone")
    started = monotonic()
    tool_calls = 1
    try:
        market = market_checker(request.ticker, request.analysis_date)
    except Exception:
        market = None
    if (
        market is None
        or market.requested_cutoff != request.analysis_date
        or market.observed_bar_date != market.market_effective_date
        or market.market_effective_date > request.analysis_date
    ):
        return _result(
            request,
            information_frontier,
            started,
            tool_calls,
            reasons=(AnchorReadinessReason.MISSING_MARKET_OBSERVATION,),
            capabilities=(
                CapabilityAttestation(
                    capability=MarketResearchCapability.MARKET_OBSERVATION,
                    satisfied=False,
                    limitations=("The permitted completed market observation was unavailable.",),
                ),
            ),
            limitations=("The permitted completed market observation was unavailable.",),
        )

    overlap_start = request.analysis_date - timedelta(days=89)
    tool_calls += 1
    try:
        payload = news_collector(
            request.ticker,
            overlap_start.isoformat(),
            request.analysis_date.isoformat(),
            information_frontier=information_frontier.isoformat(),
        )
    except Exception:
        payload = ""
    invalid_source_metadata = False
    try:
        observations = tuple(extract_source_observations_strict(payload))
    except ValueError:
        observations = ()
        invalid_source_metadata = True
    watermarks = tuple(extract_source_watermarks(payload))
    version_ids = {item.version_id for item in observations}
    reasons: list[AnchorReadinessReason] = []
    if invalid_source_metadata:
        reasons.append(AnchorReadinessReason.INVALID_SOURCE_CLOSURE)
    if (
        information_frontier.astimezone(market_timezone(request.ticker)).date()
        > request.analysis_date
    ):
        reasons.append(AnchorReadinessReason.UNSAFE_POINT_IN_TIME_BOUNDARY)
    if any(
        item.replaces_version_id is not None and item.replaces_version_id not in version_ids
        for item in observations
    ):
        reasons.append(AnchorReadinessReason.INVALID_SOURCE_CLOSURE)
    if any(
        datetime.fromisoformat(item.available_at) > information_frontier for item in observations
    ):
        reasons.append(AnchorReadinessReason.UNSAFE_POINT_IN_TIME_BOUNDARY)

    market_contract = next(
        item
        for item in JAPANESE_ANCHOR_PROFILE.source_contracts
        if item.capability is MarketResearchCapability.MARKET_OBSERVATION
    )
    market_source_set = next(
        (
            source_set
            for source_set in market_contract.acceptable_source_sets
            if source_set == (_JQUANTS_MARKET_SOURCE,)
        ),
        None,
    )
    market_sources = market_source_set or ()
    source_frontiers: list[AnchorReadinessSourceFrontier] = (
        [
            AnchorReadinessSourceFrontier(
                source=_JQUANTS_MARKET_SOURCE,
                capability=MarketResearchCapability.MARKET_OBSERVATION,
                status="complete",
                information_frontier=information_frontier,
                observed_start=market.observed_bar_date,
                observed_end=market.observed_bar_date,
                requested_start=request.analysis_date,
                requested_end=request.analysis_date,
            )
        ]
        if market_source_set is not None
        else []
    )
    capabilities: list[CapabilityAttestation] = [
        CapabilityAttestation(
            capability=MarketResearchCapability.MARKET_OBSERVATION,
            satisfied=market_source_set is not None,
            sources=market_sources,
            limitations=(
                ()
                if market_source_set is not None
                else ("No configured source set matched the J-Quants readiness adapter.",)
            ),
        )
    ]
    if market_source_set is None:
        reasons.append(AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE)
    all_limitations: list[str] = []
    required_contracts = tuple(
        contract
        for contract in JAPANESE_ANCHOR_PROFILE.source_contracts
        if contract.capability in JAPANESE_ANCHOR_PROFILE.minimum_anchor_capabilities
        and contract.capability is not MarketResearchCapability.MARKET_OBSERVATION
    )
    for contract in required_contracts:
        capability = contract.capability
        transition_start = (
            anchor_frontier.astimezone(market_timezone(request.ticker)).date()
            if anchor_frontier is not None
            else None
        )
        declared_sources = tuple(
            dict.fromkeys(
                source for source_set in contract.acceptable_source_sets for source in source_set
            )
        )
        usable_by_source: dict[str, SourceWatermark | None] = {}
        limitations_by_source: dict[str, tuple[str, ...]] = {}
        for source in declared_sources:
            candidates = tuple(item for item in watermarks if item.source == source)
            if any(
                item.temporal_scope != "point_in_time"
                or item.information_frontier is None
                or datetime.fromisoformat(item.information_frontier) != information_frontier
                or item.scanned_end > request.analysis_date.isoformat()
                for item in candidates
            ):
                reasons.append(AnchorReadinessReason.UNSAFE_POINT_IN_TIME_BOUNDARY)
            observed_records = tuple(item for item in observations if item.source == source)
            if any(
                item.returned_records > len(observed_records)
                or (
                    item.reported_records is not None
                    and item.reported_records < item.returned_records
                )
                for item in candidates
            ):
                reasons.append(AnchorReadinessReason.INVALID_SOURCE_CLOSURE)
            limitations_by_source[source] = tuple(
                dict.fromkeys(text for item in candidates for text in item.limitations)
            )
            usable_by_source[source] = _usable_source_watermark(
                source,
                watermarks=watermarks,
                information_frontier=information_frontier,
                requested_cutoff=request.analysis_date,
                transition_start=transition_start,
            )
        selected_sources = _selected_source_set(contract, usable_by_source)
        satisfied = selected_sources is not None
        if not satisfied:
            reasons.append(AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE)
        relevant_sources = selected_sources or declared_sources
        limitations = tuple(
            dict.fromkeys(
                text for source in relevant_sources for text in limitations_by_source[source]
            )
        )
        all_limitations.extend(limitations)
        capabilities.append(
            CapabilityAttestation(
                capability=capability,
                satisfied=satisfied,
                sources=selected_sources or (),
                limitations=(
                    limitations
                    if limitations
                    else ()
                    if satisfied
                    else ("No source frontier attested the required capability.",)
                ),
            )
        )
        for source in selected_sources or ():
            usable = usable_by_source[source]
            requested = usable.requested_interval
            source_frontiers.append(
                AnchorReadinessSourceFrontier(
                    source=source,
                    capability=capability,
                    status=usable.status,
                    information_frontier=datetime.fromisoformat(usable.information_frontier),
                    observed_start=date.fromisoformat(usable.scanned_start),
                    observed_end=date.fromisoformat(usable.scanned_end),
                    requested_start=(
                        date.fromisoformat(requested.start)
                        if requested is not None
                        else date.fromisoformat(usable.scanned_start)
                    ),
                    requested_end=(
                        date.fromisoformat(requested.end)
                        if requested is not None
                        else date.fromisoformat(usable.scanned_end)
                    ),
                    limitations=limitations_by_source[source],
                    limitation_kind=usable.limitation_kind,
                    returned_records=usable.returned_records,
                    reported_records=usable.reported_records,
                    record_versions_digest=source_record_versions_digest(
                        item.version_id
                        for item in observations
                        if item.source == source
                    ),
                )
            )
    return _result(
        request,
        information_frontier,
        started,
        tool_calls,
        reasons=tuple(reasons),
        capabilities=tuple(capabilities),
        source_frontiers=tuple(source_frontiers),
        limitations=tuple(dict.fromkeys(all_limitations)),
    )
