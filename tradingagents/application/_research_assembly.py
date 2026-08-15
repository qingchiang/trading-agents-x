"""Research Revision assembly and deterministic comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.research_sources import JAPANESE_EVENT_SOURCES, JapaneseResearchSource

from ._research_models import (
    _NEAR_LIVE_MARKET_REFERENCE_LIMITATION,
    ClaimChange,
    ClaimConfidence,
    ClaimRevisionDelta,
    ClaimStanding,
    CoverageAttestation,
    CoverageRequirement,
    CoverageStatus,
    CurrentResearchState,
    DecisionConfidence,
    DecisionRole,
    EffectiveEvidenceSnapshot,
    EpistemicKind,
    EvidenceSnapshotItem,
    FullResearchExecution,
    IdentityDisposition,
    IncrementalEscalationReason,
    IncrementalGateResult,
    IndeterminateReason,
    QuestionChange,
    QuestionDispositionAudit,
    QuestionDispositionKind,
    QuestionDispositionLimitation,
    QuestionRevisionDelta,
    QuestionStatus,
    ResearchChangeConclusion,
    ResearchChangeKind,
    ResearchChangeSignal,
    ResearchClaim,
    ResearchDomainCoverage,
    ResearchExecutionStrategy,
    ResearchFactor,
    ResearchObjectCoverage,
    ResearchOpinion,
    ResearchQuestion,
    ResearchRevisionDraft,
    ResearchRevisionRole,
    ResearchScenarioState,
    RevisionDelta,
    ScenarioLikelihood,
    SourceObservationInterval,
    SourceRecordKind,
    SourceRecordSnapshotItem,
    SourceRecordStatus,
    SourceRecordVersion,
    SourceWatermarkSnapshot,
    TransitionCapabilityAttestation,
    TransitionContinuityRule,
    TransitionCoverageAttestation,
    TransitionCoverageLimitation,
    UpdateSummary,
    _status_after_question_disposition,
)
from ._research_policy import (
    _calendar_gaps,
    _continuity_gaps,
    _latest_permitted_market_session,
    _source_record_published_date,
    _transition_calendar_start,
    evaluate_next_update_policy,
    market_research_capability_profile,
    required_research_sources,
)
from .contracts import (
    AnalysisRequest,
    EvidenceBundle,
    EvidenceQuality,
    EvidenceTemporalScope,
    MarketReferenceLevel,
    NumericTemporalBasis,
    RunMetrics,
    report_language_value,
)
from .evidence_admission import evaluate_evidence_admission
from .research_intervals import DateInterval, DateIntervalSet
from .source_dependencies import partition_source_dependencies


def _claim_confidence(value: float | None) -> ClaimConfidence:
    if value is None:
        return ClaimConfidence.INDETERMINATE
    if value >= 0.75:
        return ClaimConfidence.HIGH
    if value >= 0.5:
        return ClaimConfidence.MEDIUM
    return ClaimConfidence.LOW


def _decision_confidence(value: float | None) -> DecisionConfidence:
    return DecisionConfidence(_claim_confidence(value).value)


def _new_claim_id() -> str:
    return f"claim_{uuid4().hex}"


def _new_question_id() -> str:
    return f"question_{uuid4().hex}"


def _identity_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _claim_identity(claim: ResearchClaim) -> tuple[str, EpistemicKind, DecisionRole]:
    return (_identity_text(claim.statement), claim.epistemic_kind, claim.decision_role)


def _question_identity(question: ResearchQuestion) -> str:
    return _identity_text(question.question)


def _unique_identity_matches(current, candidate, key):
    current_by_key: dict[object, list[object]] = {}
    candidate_by_key: dict[object, list[object]] = {}
    for item in current:
        current_by_key.setdefault(key(item), []).append(item)
    for item in candidate:
        candidate_by_key.setdefault(key(item), []).append(item)
    matches = {}
    ambiguous = set()
    for identity, candidate_items in candidate_by_key.items():
        current_items = current_by_key.get(identity, [])
        if len(candidate_items) == len(current_items) == 1:
            matches[candidate_items[0].id] = current_items[0]
        elif current_items:
            ambiguous.update(item.id for item in candidate_items)
    return matches, ambiguous


def _source_metadata(
    bundle: EvidenceBundle,
) -> tuple[tuple[SourceRecordVersion, ...], tuple[SourceWatermarkSnapshot, ...]]:
    records: dict[str, SourceRecordVersion] = {}
    watermarks: dict[tuple[str, date, date], SourceWatermarkSnapshot] = {}
    status_rank = {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.LIMITED: 1,
        CoverageStatus.UNAVAILABLE: 2,
    }
    cutoff_timezone = market_timezone(bundle.instrument)
    for evidence in bundle.items:
        for raw in evidence.provenance.get("source_records", ()):
            record = SourceRecordVersion.model_validate(
                {
                    **raw,
                    "evidence_ref": evidence.ref,
                    "fallback": evidence.fallback,
                }
            )
            if (
                bundle.information_frontier is not None
                and record.available_at > bundle.information_frontier
            ):
                continue
            if record.available_at.astimezone(cutoff_timezone).date() > bundle.analysis_date:
                raise ValueError("Source Record Version is available after the analysis cutoff")
            existing = records.get(record.version_id)
            if existing is not None:
                if existing.model_dump(exclude={"evidence_ref"}) != record.model_dump(
                    exclude={"evidence_ref"}
                ):
                    raise ValueError("Source Record Version identity has conflicting observations")
                record = existing
            records[record.version_id] = record
        for raw in evidence.provenance.get("source_watermarks", ()):
            raw_watermark = dict(raw)
            requested_interval = raw_watermark.pop("requested_interval", None)
            limitation_kind = raw_watermark.pop("limitation_kind", None)
            observed_intervals = (
                ()
                if raw_watermark["status"] == "unavailable"
                else (
                    {
                        "start": raw_watermark["scanned_start"],
                        "end": raw_watermark["scanned_end"],
                    },
                )
            )
            structured_limitations = tuple(
                {
                    "kind": limitation_kind
                    or ("unavailable" if raw_watermark["status"] == "unavailable" else "unknown"),
                    "temporal_scope": raw_watermark.get("temporal_scope", "point_in_time"),
                    "requested_interval": requested_interval
                    or {
                        "start": raw_watermark["scanned_start"],
                        "end": raw_watermark["scanned_end"],
                    },
                    "observed_intervals": observed_intervals,
                    "presentation_text": text,
                }
                for text in raw_watermark.get("limitations", ())
            )
            watermark = SourceWatermarkSnapshot.model_validate(
                {
                    **raw_watermark,
                    "requested_interval": requested_interval,
                    "observed_intervals": observed_intervals,
                    "structured_limitations": structured_limitations,
                }
            )
            key = (watermark.source, watermark.scanned_start, watermark.scanned_end)
            existing = watermarks.get(key)
            if existing is None:
                watermarks[key] = watermark
                continue
            worse = max((existing, watermark), key=lambda item: status_rank[item.status])
            reported_values = tuple(
                value
                for value in (existing.reported_records, watermark.reported_records)
                if value is not None
            )
            watermarks[key] = SourceWatermarkSnapshot(
                source=watermark.source,
                scanned_start=watermark.scanned_start,
                scanned_end=watermark.scanned_end,
                status=worse.status,
                temporal_scope=(
                    existing.temporal_scope
                    if existing.temporal_scope == watermark.temporal_scope
                    else "unknown"
                ),
                limitations=tuple(dict.fromkeys((*existing.limitations, *watermark.limitations))),
                returned_records=max(existing.returned_records, watermark.returned_records),
                reported_records=max(reported_values) if reported_values else None,
                information_frontier=(
                    min(existing.information_frontier, watermark.information_frontier)
                    if existing.information_frontier is not None
                    and watermark.information_frontier is not None
                    else None
                ),
                requested_interval=(existing.requested_interval or watermark.requested_interval),
                observed_intervals=tuple(
                    dict.fromkeys((*existing.observed_intervals, *watermark.observed_intervals))
                ),
                structured_limitations=tuple(
                    dict.fromkeys(
                        (*existing.structured_limitations, *watermark.structured_limitations)
                    )
                ),
            )
    return tuple(records.values()), tuple(watermarks.values())


def _source_coverage(
    state: CurrentResearchState,
    records: tuple[SourceRecordVersion, ...],
    watermarks: tuple[SourceWatermarkSnapshot, ...],
    *,
    status_blocking_records: tuple[SourceRecordVersion, ...] | None = None,
    observed_records: tuple[SourceRecordVersion, ...] | None = None,
    required_data_domains: tuple[str, ...] = (),
) -> tuple[tuple[ResearchDomainCoverage, ...], bool]:
    explicitly_required = set(required_research_sources(state))
    refs_by_source: dict[str, list[str]] = {}
    for record in records:
        refs_by_source.setdefault(record.source, []).append(record.evidence_ref)
    domains = []
    supports_quiet = True
    watermarks_by_source: dict[str, list[SourceWatermarkSnapshot]] = {}
    for watermark in watermarks:
        watermarks_by_source.setdefault(watermark.source, []).append(watermark)
    observed_sources = set(watermarks_by_source)
    status_rank = {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.LIMITED: 1,
        CoverageStatus.UNAVAILABLE: 2,
    }
    for source_watermarks in watermarks_by_source.values():
        watermark = max(source_watermarks, key=lambda item: status_rank[item.status])
        domain_name = {
            JapaneseResearchSource.GOOGLE_NEWS: "media_news",
            JapaneseResearchSource.JQUANTS_FUNDAMENTALS: "fundamentals",
            JapaneseResearchSource.JQUANTS_ADJUSTED_OHLCV: "market",
        }.get(watermark.source, "company_disclosures")
        required = (
            watermark.source in explicitly_required
            or (state.instrument.endswith(".T") and watermark.source in JAPANESE_EVENT_SOURCES)
            or domain_name in required_data_domains
        )
        advisory = not required
        requirement = CoverageRequirement.ADVISORY if advisory else CoverageRequirement.REQUIRED
        live_only_required = requirement is CoverageRequirement.REQUIRED and any(
            item.temporal_scope != "point_in_time" for item in source_watermarks
        )
        count_inconsistent = any(
            item.reported_records is not None and item.reported_records < item.returned_records
            for item in source_watermarks
        )
        positive_without_observed_version = (
            requirement is CoverageRequirement.REQUIRED
            and any(item.returned_records > 0 for item in source_watermarks)
            and not any(
                item.source == watermark.source
                for item in (records if observed_records is None else observed_records)
            )
        )
        normalized_intervals = DateIntervalSet(
            tuple(
                DateInterval(item.scanned_start, item.scanned_end)
                for item in source_watermarks
            )
        )
        missing_interval = bool(
            normalized_intervals.gaps(
                normalized_intervals.intervals[0].start,
                normalized_intervals.intervals[-1].end,
            )
        )
        if requirement is CoverageRequirement.REQUIRED and (
            watermark.status is not CoverageStatus.COMPLETE
            or live_only_required
            or missing_interval
            or count_inconsistent
            or positive_without_observed_version
        ):
            supports_quiet = False
        limitations = (
            tuple(dict.fromkeys(value for item in source_watermarks for value in item.limitations))
            + (("Required source coverage is not point-in-time.",) if live_only_required else ())
            + (
                ("Source watermark intervals contain an unscanned gap.",)
                if missing_interval
                else ()
            )
            + (("Source watermark record counts are inconsistent.",) if count_inconsistent else ())
            + (
                ("Positive source results have no observed Source Record Version.",)
                if positive_without_observed_version
                else ()
            )
        )
        domains.append(
            ResearchDomainCoverage(
                domain=domain_name,
                source=watermark.source,
                requirement=requirement,
                status=(
                    CoverageStatus.LIMITED
                    if (
                        live_only_required
                        or missing_interval
                        or count_inconsistent
                        or positive_without_observed_version
                    )
                    and watermark.status is CoverageStatus.COMPLETE
                    else watermark.status
                ),
                evidence_refs=tuple(dict.fromkeys(refs_by_source.get(watermark.source, ()))),
                limitations=limitations,
            )
        )
    if state.instrument.endswith(".T"):
        for source in JAPANESE_EVENT_SOURCES:
            if source in observed_sources:
                continue
            supports_quiet = False
            domains.append(
                ResearchDomainCoverage(
                    domain="company_disclosures",
                    source=source,
                    requirement=CoverageRequirement.REQUIRED,
                    status=CoverageStatus.UNAVAILABLE,
                    limitations=(f"{source} collection coverage was not recorded.",),
                )
            )
        required_sources = {
            "fundamentals": JapaneseResearchSource.JQUANTS_FUNDAMENTALS,
            "market": JapaneseResearchSource.JQUANTS_ADJUSTED_OHLCV,
        }
        for domain_name, source in required_sources.items():
            if domain_name not in required_data_domains or source in observed_sources:
                continue
            supports_quiet = False
            domains.append(
                ResearchDomainCoverage(
                    domain=domain_name,
                    source=source,
                    requirement=CoverageRequirement.REQUIRED,
                    status=CoverageStatus.UNAVAILABLE,
                    limitations=(f"{source} collection coverage was not recorded.",),
                )
            )
    for source in sorted(explicitly_required - observed_sources):
        if source in JAPANESE_EVENT_SOURCES and state.instrument.endswith(".T"):
            continue
        supports_quiet = False
        domains.append(
            ResearchDomainCoverage(
                domain="required_source",
                source=source,
                requirement=CoverageRequirement.REQUIRED,
                status=CoverageStatus.UNAVAILABLE,
                limitations=(f"Required {source} collection coverage was not recorded.",),
            )
        )
    if any(
        record.status is not SourceRecordStatus.PUBLISHED
        and record.source in JAPANESE_EVENT_SOURCES
        for record in (records if status_blocking_records is None else status_blocking_records)
    ):
        supports_quiet = False
    return tuple(domains), supports_quiet


def _crossed(previous: float, current: float, boundary: float) -> bool:
    return (previous < boundary <= current) or (previous > boundary >= current)


def _change_signals(
    baseline: ResearchRevisionDraft,
    candidate_records: tuple[SourceRecordVersion, ...],
) -> tuple[ResearchChangeSignal, ...]:
    """Compare producer-owned snapshots without parsing analyst prose."""
    baseline_by_record: dict[str, list[SourceRecordVersion]] = {}
    for record in baseline.evidence_snapshot.source_records:
        key = (
            record.comparison_key
            if record.record_kind is SourceRecordKind.FUNDAMENTAL
            else record.record_id
        )
        baseline_by_record.setdefault(key or record.record_id, []).append(record)
    candidate_by_record: dict[str, list[SourceRecordVersion]] = {}
    for record in candidate_records:
        if record.record_kind is not SourceRecordKind.DISCLOSURE:
            key = (
                record.comparison_key
                if record.record_kind is SourceRecordKind.FUNDAMENTAL
                else record.record_id
            )
            candidate_by_record.setdefault(key or record.record_id, []).append(record)

    signals: list[ResearchChangeSignal] = []
    for comparison_key, current_versions in candidate_by_record.items():
        previous_versions = baseline_by_record.get(comparison_key, [])
        previous_ids = {item.version_id for item in previous_versions}
        new_versions = [item for item in current_versions if item.version_id not in previous_ids]
        record_kind = current_versions[-1].record_kind
        record_id = current_versions[-1].record_id
        if not new_versions:
            previous = previous_versions[-1] if previous_versions else None
            current = current_versions[-1]
            signals.append(
                ResearchChangeSignal(
                    kind=ResearchChangeKind.UNCHANGED_OBSERVATION,
                    domain=(
                        "fundamentals" if record_kind is SourceRecordKind.FUNDAMENTAL else "market"
                    ),
                    record_id=record_id,
                    previous_version_id=previous.version_id if previous else None,
                    current_version_id=current.version_id,
                    requires_full_analysis=False,
                    detail="The source-native version was re-observed without change.",
                    previous_value=previous.observation_value if previous else None,
                    current_value=current.observation_value,
                )
            )
            continue

        if record_kind is SourceRecordKind.FUNDAMENTAL:
            known = {item.version_id: item for item in (*previous_versions, *current_versions)}
            for current in new_versions:
                previous = known.get(current.replaces_version_id or "")
                if previous is None and previous_versions:
                    previous = previous_versions[-1]
                if previous is None:
                    kind = ResearchChangeKind.NEW_FUNDAMENTAL_FILING
                    detail = "A new official fundamental reporting period was observed."
                elif current.change_hint == "accounting_scope_change" or (
                    previous is not None and current.accounting_scope != previous.accounting_scope
                ):
                    kind = ResearchChangeKind.ACCOUNTING_SCOPE_CHANGE
                    detail = "The accounting standard or consolidation scope changed."
                elif current.change_hint == "restatement":
                    kind = ResearchChangeKind.FUNDAMENTAL_RESTATEMENT
                    detail = "Previously reported fundamental values were restated."
                elif current.change_hint == "correction" or (
                    current.status is SourceRecordStatus.CORRECTED
                ):
                    kind = ResearchChangeKind.FUNDAMENTAL_CORRECTION
                    detail = "An official correction changed the observed version."
                else:
                    kind = ResearchChangeKind.UNCLASSIFIABLE_FUNDAMENTAL_CHANGE
                    detail = "The fundamental snapshot changed without a safe classification."
                signals.append(
                    ResearchChangeSignal(
                        kind=kind,
                        domain="fundamentals",
                        record_id=record_id,
                        previous_version_id=previous.version_id if previous else None,
                        current_version_id=current.version_id,
                        requires_full_analysis=True,
                        detail=detail,
                    )
                )
            continue

        previous = previous_versions[-1] if previous_versions else None
        current = new_versions[-1]
        incompatible = previous is None or any(
            (
                previous.source != current.source,
                previous.adjustment != current.adjustment,
                previous.unit != current.unit,
            )
        )
        if incompatible:
            signals.append(
                ResearchChangeSignal(
                    kind=ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY,
                    domain="market",
                    record_id=record_id,
                    previous_version_id=previous.version_id if previous else None,
                    current_version_id=current.version_id,
                    requires_full_analysis=True,
                    detail="Provider, adjustment, or unit semantics are not baseline-compatible.",
                    previous_value=previous.observation_value if previous else None,
                    current_value=current.observation_value,
                )
            )
            continue
        crossing = next(
            (
                boundary
                for boundary in baseline.current_state.market_reference_levels
                if previous.observation_value is not None
                and current.observation_value is not None
                and boundary.unit == current.unit
                and _crossed(previous.observation_value, current.observation_value, boundary.value)
            ),
            None,
        )
        signals.append(
            ResearchChangeSignal(
                kind=(
                    ResearchChangeKind.MARKET_BOUNDARY_CROSSING
                    if crossing is not None
                    else ResearchChangeKind.ORDINARY_MARKET_MOVE
                ),
                domain="market",
                record_id=record_id,
                previous_version_id=previous.version_id,
                current_version_id=current.version_id,
                requires_full_analysis=crossing is not None,
                detail=(
                    "The observed market value crossed a thesis-relevant reference."
                    if crossing is not None
                    else "The market value changed without crossing a recorded boundary."
                ),
                boundary_label=crossing.label if crossing is not None else None,
                boundary_value=crossing.value if crossing is not None else None,
                previous_value=previous.observation_value,
                current_value=current.observation_value,
            )
        )
    return tuple(signals)


@dataclass(frozen=True)
class _SourceTransitionResult:
    complete: bool
    checked_intervals: tuple[SourceObservationInterval, ...]
    gaps: tuple[SourceObservationInterval, ...]
    limitations: tuple[TransitionCoverageLimitation, ...]


_MISSING_SOURCE_TRANSITION = _SourceTransitionResult(False, (), (), ())


def _transition_coverage(
    baseline: ResearchRevisionDraft,
    update_frontier: datetime,
    watermarks: tuple[SourceWatermarkSnapshot, ...],
    observed_records: tuple[SourceRecordVersion, ...],
) -> TransitionCoverageAttestation:
    anchor_frontier = baseline.information_frontier
    if anchor_frontier is None:
        raise ValueError("Forward Research Anchor requires an Information Frontier")
    profile = market_research_capability_profile(baseline.current_state.instrument)
    if profile is None:
        raise ValueError("Transition Coverage requires an audited market profile")
    market_tz = market_timezone(baseline.current_state.instrument)
    transition_start = _transition_calendar_start(anchor_frontier, market_tz)
    transition_end = update_frontier.astimezone(market_tz).date()
    required_capabilities = {
        item.capability
        for item in (
            baseline.coverage.anchor_qualification.capabilities
            if baseline.coverage.anchor_qualification is not None
            else ()
        )
        if item.required
    } or set(profile.minimum_anchor_capabilities)
    by_source = {
        source: tuple(item for item in watermarks if item.source == source)
        for source in {item.source for item in watermarks}
    }
    observed_sources = {item.source for item in observed_records}
    attestations: list[TransitionCapabilityAttestation] = []
    for contract in profile.source_contracts:
        if contract.capability not in required_capabilities:
            continue
        source_results: dict[str, _SourceTransitionResult] = {}
        for source_set in contract.acceptable_source_sets:
            for source in source_set:
                if source in source_results:
                    continue
                source_watermarks = by_source.get(source, ())
                transition_watermarks = tuple(
                    watermark
                    for watermark in source_watermarks
                    if any(
                        interval.end >= transition_start and interval.start <= transition_end
                        for interval in watermark.observed_intervals
                    )
                )
                checked = tuple(
                    dict.fromkeys(
                        interval
                        for watermark in source_watermarks
                        for interval in watermark.observed_intervals
                    )
                )
                gaps = _continuity_gaps(
                    contract.transition_continuity,
                    transition_start,
                    transition_end,
                    checked,
                )
                limitations = []
                for watermark in source_watermarks:
                    for limitation in watermark.structured_limitations:
                        limitation_gaps = _calendar_gaps(
                            limitation.requested_interval.start,
                            limitation.requested_interval.end,
                            limitation.observed_intervals,
                        )
                        pre_anchor = limitation.requested_interval.end < transition_start or (
                            bool(limitation_gaps)
                            and all(item.end < transition_start for item in limitation_gaps)
                        )
                        limitations.append(
                            TransitionCoverageLimitation(
                                kind=limitation.kind,
                                scope="pre_anchor" if pre_anchor else "transition",
                                temporal_scope=limitation.temporal_scope,
                                source=source,
                                requested_interval=limitation.requested_interval,
                                observed_intervals=limitation.observed_intervals,
                                presentation_text=limitation.presentation_text,
                            )
                        )
                complete = (
                    bool(transition_watermarks)
                    and not gaps
                    and all(
                        watermark.temporal_scope == "point_in_time"
                        and watermark.information_frontier is not None
                        and watermark.information_frontier >= update_frontier
                        and (
                            watermark.reported_records == 0
                            if watermark.returned_records == 0
                            else watermark.reported_records is None
                            or watermark.reported_records >= watermark.returned_records
                        )
                        and (watermark.returned_records == 0 or source in observed_sources)
                        and (
                            contract.transition_continuity
                            is not TransitionContinuityRule.MARKET_SERIES
                            or (
                                watermark.returned_records > 0
                                and any(
                                    item.source == source
                                    and _source_record_published_date(item)
                                    == _latest_permitted_market_session(transition_end)
                                    for item in observed_records
                                )
                            )
                        )
                        for watermark in transition_watermarks
                    )
                )
                if any(item.scope == "transition" for item in limitations):
                    complete = False
                if not limitations and any(
                    watermark.temporal_scope != "point_in_time"
                    for watermark in transition_watermarks
                ):
                    for watermark in transition_watermarks:
                        if watermark.temporal_scope == "point_in_time":
                            continue
                        requested = watermark.requested_interval or SourceObservationInterval(
                            start=watermark.scanned_start,
                            end=watermark.scanned_end,
                        )
                        limitations.append(
                            TransitionCoverageLimitation(
                                kind=(
                                    "live_only"
                                    if watermark.temporal_scope == "live_only"
                                    else "unknown"
                                ),
                                scope="transition",
                                temporal_scope=watermark.temporal_scope,
                                source=source,
                                requested_interval=requested,
                                observed_intervals=watermark.observed_intervals,
                                presentation_text=(
                                    "Required source temporal scope cannot prove the transition."
                                ),
                            )
                        )
                if any(
                    watermark.status is CoverageStatus.LIMITED
                    and not watermark.structured_limitations
                    for watermark in transition_watermarks
                ):
                    complete = False
                source_results[source] = _SourceTransitionResult(
                    complete=complete,
                    checked_intervals=checked,
                    gaps=gaps,
                    limitations=tuple(limitations),
                )
        satisfied_sources = next(
            (
                source_set
                for source_set in contract.acceptable_source_sets
                if all(source_results[source].complete for source in source_set)
            ),
            (),
        )
        reported_sources = satisfied_sources or contract.acceptable_source_sets[0]
        attestations.append(
            TransitionCapabilityAttestation(
                capability=contract.capability,
                complete=bool(satisfied_sources),
                sources=reported_sources,
                checked_intervals=tuple(
                    dict.fromkeys(
                        item
                        for source in reported_sources
                        for item in source_results.get(
                            source, _MISSING_SOURCE_TRANSITION
                        ).checked_intervals
                    )
                ),
                gaps=tuple(
                    dict.fromkeys(
                        item
                        for source in reported_sources
                        for item in source_results.get(source, _MISSING_SOURCE_TRANSITION).gaps
                    )
                ),
                limitations=tuple(
                    dict.fromkeys(
                        item
                        for source in reported_sources
                        for item in source_results.get(
                            source, _MISSING_SOURCE_TRANSITION
                        ).limitations
                    )
                ),
            )
        )
    return TransitionCoverageAttestation(
        anchor_frontier=anchor_frontier,
        update_frontier=update_frontier,
        complete=all(item.complete for item in attestations),
        capabilities=tuple(attestations),
    )


def assess_deterministic_update(
    baseline_revision_id: str,
    baseline: ResearchRevisionDraft,
    request: AnalysisRequest,
    evidence: EvidenceBundle,
    *,
    metrics: RunMetrics | None = None,
    mode: Literal["off", "shadow", "experimental"] = "experimental",
    information_frontier: datetime | None = None,
) -> IncrementalGateResult:
    """Apply fail-closed gates and build a quiet bounded-update candidate."""

    if (
        evaluate_next_update_policy(
            baseline,
            instrument=request.ticker,
            mode=mode,
        ).policy
        != "incremental_allowed"
        or request.ticker != baseline.current_state.instrument
        or request.analysis_date <= baseline.cutoff
        or evidence.instrument != request.ticker
        or evidence.analysis_date != request.analysis_date
    ):
        return IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.INVALID_BASELINE,
            metrics=metrics or RunMetrics(),
        )
    try:
        update_frontier = information_frontier or evidence.information_frontier
        if update_frontier is None:
            raise ValueError("bounded update requires an explicit Information Frontier")
        if update_frontier.utcoffset() is None:
            raise ValueError("bounded update requires a timezone-aware Information Frontier")
        bounded_evidence = evidence.model_copy(update={"information_frontier": update_frontier})
        candidate_records, candidate_watermarks = _source_metadata(bounded_evidence)
        baseline_records = {
            item.version_id: item for item in baseline.evidence_snapshot.source_records
        }
        newly_observed = tuple(
            item for item in candidate_records if item.version_id not in baseline_records
        )
        state_refs = {
            ref
            for claim in baseline.current_state.claims
            if claim.standing is ClaimStanding.ACTIVE
            for ref in claim.evidence_refs
        }
        state_refs.update(
            ref
            for question in baseline.current_state.questions
            if question.status is QuestionStatus.OPEN
            for ref in question.evidence_refs
        )
        required_domains = {
            (
                "fundamentals"
                if record.record_kind is SourceRecordKind.FUNDAMENTAL
                else "market"
                if record.record_kind is SourceRecordKind.MARKET
                else "company_disclosures"
            )
            for record in baseline.evidence_snapshot.source_records
            if record.evidence_ref in state_refs
        }
        if baseline.current_state.market_reference_levels:
            required_domains.add("market")
        transition_start = _transition_calendar_start(
            baseline.information_frontier,
            market_timezone(request.ticker),
        )
        profile = market_research_capability_profile(request.ticker)
        continuity_by_source = {
            source: contract.transition_continuity
            for contract in (profile.source_contracts if profile is not None else ())
            for source_set in contract.acceptable_source_sets
            for source in source_set
        }
        watermarks = tuple(
            item.model_copy(
                update={
                    "baseline_cutoff": baseline.cutoff,
                    "overlap_start": (
                        item.scanned_start
                        if item.scanned_start <= baseline.cutoff <= item.scanned_end
                        else None
                    ),
                    "status": (
                        CoverageStatus.LIMITED
                        if (
                            (
                                continuity_by_source.get(item.source)
                                is TransitionContinuityRule.EVENT_STREAM
                                and not item.scanned_start <= transition_start <= item.scanned_end
                            )
                            or item.scanned_end != request.analysis_date
                        )
                        and item.status is CoverageStatus.COMPLETE
                        else item.status
                    ),
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *item.limitations,
                                *(
                                    ()
                                    if continuity_by_source.get(item.source)
                                    is not TransitionContinuityRule.EVENT_STREAM
                                    or item.scanned_start <= transition_start <= item.scanned_end
                                    else (
                                        "Collection window did not overlap the required transition.",
                                    )
                                ),
                                *(
                                    ()
                                    if item.scanned_end == request.analysis_date
                                    else ("Collection window did not end at the update cutoff.",)
                                ),
                            )
                        )
                    ),
                    "information_frontier": (
                        min(item.information_frontier, update_frontier)
                        if item.information_frontier is not None
                        else None
                    ),
                }
            )
            for item in candidate_watermarks
        )
        transition_coverage = _transition_coverage(
            baseline,
            update_frontier,
            watermarks,
            candidate_records,
        )
        combined_records = tuple(
            {
                item.version_id: item for item in (*baseline_records.values(), *candidate_records)
            }.values()
        )
        domains, supports_quiet = _source_coverage(
            baseline.current_state,
            combined_records,
            watermarks,
            status_blocking_records=newly_observed,
            observed_records=candidate_records,
            required_data_domains=tuple(sorted(required_domains)),
        )
        transition_sources = {
            source
            for capability in transition_coverage.capabilities
            if capability.complete
            for source in capability.sources
        }
        domains = tuple(
            domain.model_copy(update={"status": CoverageStatus.COMPLETE})
            if domain.requirement is CoverageRequirement.REQUIRED
            and domain.source in transition_sources
            else domain
            for domain in domains
        )
        supports_quiet = transition_coverage.complete and all(
            domain.status is CoverageStatus.COMPLETE
            for domain in domains
            if domain.requirement is CoverageRequirement.REQUIRED
        )
        signals = _change_signals(baseline, candidate_records)
        reason = None
        status_reasons = {
            SourceRecordStatus.CORRECTED: IncrementalEscalationReason.SOURCE_CORRECTION,
            SourceRecordStatus.WITHDRAWN: IncrementalEscalationReason.SOURCE_WITHDRAWAL,
            SourceRecordStatus.REPLACED: IncrementalEscalationReason.SOURCE_REPLACEMENT,
        }
        for record in newly_observed:
            if record.status in status_reasons:
                reason = status_reasons[record.status]
                break
        if reason is None:
            for signal in signals:
                if signal.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY:
                    reason = IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
                    break
                if signal.kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING:
                    reason = IncrementalEscalationReason.THRESHOLD_CROSSING
                    break
                if signal.requires_full_analysis:
                    reason = IncrementalEscalationReason.SOURCE_VERSION_CHANGE
                    break
        if reason is None and any(
            item.record_kind is SourceRecordKind.DISCLOSURE for item in newly_observed
        ):
            reason = IncrementalEscalationReason.SOURCE_VERSION_CHANGE
        if reason is None and not supports_quiet:
            reason = IncrementalEscalationReason.COVERAGE_INCOMPLETE
        baseline_bundle = baseline.evidence_snapshot.bundle
        new_refs = {item.ref for item in bounded_evidence.items}
        combined_items = tuple(
            {item.ref: item for item in (*baseline_bundle.items, *bounded_evidence.items)}.values()
        )
        combined_tables = tuple(
            {item.id: item for item in (*baseline_bundle.tables, *bounded_evidence.tables)}.values()
        )
        bundle = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            information_frontier=update_frontier,
            items=combined_items,
            tables=combined_tables,
            sealed_at=bounded_evidence.sealed_at,
        )
        evidence_snapshot = EffectiveEvidenceSnapshot(
            bundle=bundle,
            lineage=tuple(
                EvidenceSnapshotItem(
                    evidence_ref=item.ref,
                    lineage="new" if item.ref in new_refs else "inherited",
                    source_revision_id=(None if item.ref in new_refs else baseline_revision_id),
                )
                for item in combined_items
            ),
            source_records=combined_records,
            source_record_lineage=tuple(
                SourceRecordSnapshotItem(
                    version_id=item.version_id,
                    lineage=("new" if item.version_id not in baseline_records else "inherited"),
                    observed_in_execution=item.version_id
                    in {record.version_id for record in candidate_records},
                    source_revision_id=(
                        None if item.version_id not in baseline_records else baseline_revision_id
                    ),
                )
                for item in combined_records
            ),
            source_watermarks=watermarks,
        )
        coverage_status = CoverageStatus.COMPLETE if supports_quiet else CoverageStatus.LIMITED
        limitations = tuple(
            dict.fromkeys(value for domain in domains for value in domain.limitations)
        )
        coverage = CoverageAttestation(
            claims=tuple(
                ResearchObjectCoverage(
                    object_id=item.id,
                    status=coverage_status,
                    evidence_refs=item.evidence_refs,
                )
                for item in baseline.current_state.claims
            ),
            questions=tuple(
                ResearchObjectCoverage(
                    object_id=item.id,
                    status=coverage_status,
                )
                for item in baseline.current_state.questions
            ),
            domains=domains,
            limitations=limitations,
            supports_no_material_change=supports_quiet,
        )
        if reason is not None:
            return IncrementalGateResult(
                escalation_reason=reason,
                coverage=coverage,
                evidence_snapshot=evidence_snapshot,
                transition_coverage=transition_coverage,
                metrics=metrics or RunMetrics(),
            )
        candidate = ResearchRevisionDraft(
            cutoff=request.analysis_date,
            information_frontier=update_frontier,
            role=ResearchRevisionRole.UPDATE,
            execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
            change_conclusion=ResearchChangeConclusion.NO_MATERIAL_CHANGE,
            delta=RevisionDelta(
                opinion_changed=False,
                claims=tuple(
                    ClaimRevisionDelta(
                        object_id=item.id,
                        previous_object_id=item.id,
                        change=ClaimChange.REAFFIRMED,
                        identity_disposition=IdentityDisposition.EXACT_MATCH,
                    )
                    for item in baseline.current_state.claims
                    if item.standing is ClaimStanding.ACTIVE
                ),
                questions=tuple(
                    QuestionRevisionDelta(
                        object_id=item.id,
                        previous_object_id=item.id,
                        change=QuestionChange.REAFFIRMED,
                        identity_disposition=IdentityDisposition.EXACT_MATCH,
                    )
                    for item in baseline.current_state.questions
                    if item.status in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}
                ),
                inherited_evidence_refs=tuple(
                    item.ref for item in baseline_bundle.items if item.ref not in new_refs
                ),
                new_evidence_refs=tuple(item.ref for item in bounded_evidence.items),
                change_signals=signals,
            ),
            current_state=baseline.current_state.model_copy(
                update={
                    "cutoff": request.analysis_date,
                    "evidence_refs": tuple(item.ref for item in combined_items),
                    "scenarios": tuple(
                        item.model_copy(update={"cutoff": request.analysis_date})
                        for item in baseline.current_state.scenarios
                    ),
                }
            ),
            coverage=coverage,
            update_summary=UpdateSummary(
                language=baseline.current_state.language,
                summary=(
                    "Deterministic gates found no material change; the bounded candidate "
                    "is ready for the configured research-update mode."
                ),
                checked_domains=tuple(dict.fromkeys(item.domain for item in domains)),
                limitations=limitations,
                baseline_cutoff=baseline.cutoff,
                analysis_cutoff=request.analysis_date,
                execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
                change_conclusion=ResearchChangeConclusion.NO_MATERIAL_CHANGE,
                new_evidence_refs=tuple(item.ref for item in bounded_evidence.items),
            ),
            evidence_snapshot=evidence_snapshot,
        )
    except (TypeError, ValueError):
        return IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.SCHEMA_INVALID,
            metrics=metrics or RunMetrics(),
        )
    return IncrementalGateResult(
        candidate=candidate,
        coverage=coverage,
        evidence_snapshot=evidence_snapshot,
        transition_coverage=transition_coverage,
        metrics=metrics or RunMetrics(),
    )


def assemble_full_update(
    baseline_revision_id: str,
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
) -> ResearchRevisionDraft:
    """Compare an independently assembled Full result with the current chain head."""
    if candidate.current_state.instrument != baseline.current_state.instrument:
        raise ValueError("update Instrument must match the current Research Chain head")
    if candidate.cutoff <= baseline.cutoff:
        raise ValueError("update cutoff must be strictly later than the current Research Chain head")

    claim_matches, ambiguous_claims = _unique_identity_matches(
        baseline.current_state.claims,
        candidate.current_state.claims,
        _claim_identity,
    )
    question_matches, ambiguous_questions = _unique_identity_matches(
        baseline.current_state.questions,
        candidate.current_state.questions,
        _question_identity,
    )
    independently_introduced_question_ids = {
        item.id
        for item in candidate.current_state.questions
        if item.id not in question_matches and item.id not in ambiguous_questions
    }
    question_disposition = candidate.delta.question_disposition
    if baseline.current_state.questions and question_disposition is None:
        question_disposition = QuestionDispositionAudit(
            status="limited",
            language=candidate.current_state.language,
            limitation_reason=QuestionDispositionLimitation.INCOMPLETE,
        )
    if question_disposition is not None and question_disposition.status == "complete":
        baseline_questions_by_id = {item.id: item for item in baseline.current_state.questions}
        question_matches = {
            item.candidate_question_id: baseline_questions_by_id[item.baseline_question_id]
            for item in question_disposition.dispositions
            if item.candidate_question_id is not None
        }
        ambiguous_questions = set()
    elif baseline.current_state.questions:
        question_matches = {}
        ambiguous_questions = set()
    claim_ids = {candidate_id: previous.id for candidate_id, previous in claim_matches.items()}
    question_ids = {
        candidate_id: previous.id for candidate_id, previous in question_matches.items()
    }
    dependency_compatibility_repaired = False

    def candidate_dependencies(values: tuple[str, ...]) -> tuple[str, ...]:
        nonlocal dependency_compatibility_repaired
        external, internal = partition_source_dependencies(values)
        dependency_compatibility_repaired = (
            dependency_compatibility_repaired or bool(internal)
        )
        return external

    def inherited_dependencies(
        previous: tuple[str, ...],
        candidate: tuple[str, ...],
    ) -> tuple[str, ...]:
        nonlocal dependency_compatibility_repaired
        previous_external, previous_internal = partition_source_dependencies(previous)
        candidate_external = candidate_dependencies(candidate)
        if not previous_internal:
            return previous_external
        dependency_compatibility_repaired = True
        return tuple(dict.fromkeys((*previous_external, *candidate_external)))

    claims = tuple(
        claim.model_copy(
            update={
                "id": claim_ids.get(claim.id, claim.id),
                "required_sources": (
                    inherited_dependencies(
                        claim_matches[claim.id].required_sources,
                        claim.required_sources,
                    )
                    if claim.id in claim_matches
                    else candidate_dependencies(claim.required_sources)
                ),
            }
        )
        for claim in candidate.current_state.claims
    )
    questions = tuple(
        question.model_copy(
            update={
                "id": question_ids.get(question.id, question.id),
                "required_sources": (
                    inherited_dependencies(
                        question_matches[question.id].required_sources,
                        question.required_sources,
                    )
                    if question.id in question_matches
                    else candidate_dependencies(question.required_sources)
                ),
            }
        )
        for question in candidate.current_state.questions
    )
    retained_claim_ids = {claim.id for claim in claims}
    retained_question_ids = {question.id for question in questions}
    retired_claims = tuple(
        claim.model_copy(
            update={
                "standing": ClaimStanding.RETIRED,
                "required_sources": candidate_dependencies(claim.required_sources),
            }
        )
        for claim in baseline.current_state.claims
        if claim.id not in retained_claim_ids
    )
    retired_questions = tuple(
        question.model_copy(
            update={
                "required_sources": candidate_dependencies(question.required_sources)
            }
        )
        for question in baseline.current_state.questions
        if question.id not in retained_question_ids
    )
    if question_disposition is not None and question_disposition.status == "complete":
        candidate_questions_by_id = {item.id: item for item in candidate.current_state.questions}
        baseline_questions_by_id = {item.id: item for item in baseline.current_state.questions}
        assigned_candidate_ids = {
            value
            for item in question_disposition.dispositions
            for value in (item.candidate_question_id, item.successor_question_id)
            if value is not None
        }
        disposed_questions: list[ResearchQuestion] = []
        for item in question_disposition.dispositions:
            previous = baseline_questions_by_id[item.baseline_question_id]
            source = (
                candidate_questions_by_id[item.candidate_question_id]
                if item.candidate_question_id is not None
                else previous
            )
            disposed_questions.append(
                source.model_copy(
                    update={
                        "id": previous.id,
                        "status": _status_after_question_disposition(
                            item.disposition, previous.status
                        ),
                        "evidence_refs": tuple(
                            dict.fromkeys((*source.evidence_refs, *item.evidence_refs))
                        ),
                        "required_sources": inherited_dependencies(
                            previous.required_sources,
                            source.required_sources,
                        ),
                        "successor_question_id": item.successor_question_id,
                        "last_disposition": item.disposition,
                        "disposition_reason": item.reason,
                    }
                )
            )
        questions = (
            *disposed_questions,
            *(
                item.model_copy(
                    update={
                        "required_sources": candidate_dependencies(
                            item.required_sources
                        )
                    }
                )
                for item in candidate.current_state.questions
                if item.id not in assigned_candidate_ids
            ),
            *(
                candidate_questions_by_id[item.successor_question_id].model_copy(
                    update={
                        "required_sources": candidate_dependencies(
                            candidate_questions_by_id[
                                item.successor_question_id
                            ].required_sources
                        )
                    }
                )
                for item in question_disposition.dispositions
                if item.successor_question_id is not None
            ),
        )
        retained_question_ids = {question.id for question in questions}
        retired_questions = ()

    def remap_claim_ids(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(claim_ids.get(value, value) for value in values)

    opinion = candidate.current_state.opinion.model_copy(
        update={
            "primary_claim_ids": remap_claim_ids(candidate.current_state.opinion.primary_claim_ids)
        }
    )
    scenarios = tuple(
        item.model_copy(update={"assumption_claim_ids": remap_claim_ids(item.assumption_claim_ids)})
        for item in candidate.current_state.scenarios
    )

    def remap_factors(values: tuple[ResearchFactor, ...]) -> tuple[ResearchFactor, ...]:
        return tuple(
            item.model_copy(update={"claim_ids": remap_claim_ids(item.claim_ids)})
            for item in values
        )

    baseline_bundle = baseline.evidence_snapshot.bundle
    candidate_bundle = candidate.evidence_snapshot.bundle
    new_refs = {item.ref for item in candidate_bundle.items}
    combined_items = tuple(
        {item.ref: item for item in (*baseline_bundle.items, *candidate_bundle.items)}.values()
    )
    combined_tables = tuple(
        {item.id: item for item in (*baseline_bundle.tables, *candidate_bundle.tables)}.values()
    )
    combined_bundle = EvidenceBundle(
        instrument=candidate_bundle.instrument,
        analysis_date=candidate_bundle.analysis_date,
        items=combined_items,
        tables=combined_tables,
        sealed_at=candidate_bundle.sealed_at,
    )
    baseline_versions = {
        item.version_id: item for item in baseline.evidence_snapshot.source_records
    }
    candidate_versions = {
        item.version_id: item for item in candidate.evidence_snapshot.source_records
    }
    combined_versions = tuple((baseline_versions | candidate_versions).values())
    current_version_ids = set(candidate_versions)
    source_record_lineage = tuple(
        SourceRecordSnapshotItem(
            version_id=item.version_id,
            lineage="inherited" if item.version_id in baseline_versions else "new",
            observed_in_execution=item.version_id in current_version_ids,
            source_revision_id=(
                baseline_revision_id if item.version_id in baseline_versions else None
            ),
        )
        for item in combined_versions
    )
    source_watermarks = []
    for item in candidate.evidence_snapshot.source_watermarks:
        covers_baseline = item.scanned_start <= baseline.cutoff <= item.scanned_end
        overlap_limitation = (
            ()
            if covers_baseline
            else ("Collection window did not overlap the current head cutoff.",)
        )
        source_watermarks.append(
            item.model_copy(
                update={
                    "baseline_cutoff": baseline.cutoff,
                    "overlap_start": item.scanned_start if covers_baseline else None,
                    "status": (
                        CoverageStatus.LIMITED
                        if overlap_limitation and item.status is CoverageStatus.COMPLETE
                        else item.status
                    ),
                    "limitations": tuple(dict.fromkeys((*item.limitations, *overlap_limitation))),
                }
            )
        )
    source_watermarks = tuple(source_watermarks)
    inherited_refs = tuple(item.ref for item in baseline_bundle.items if item.ref not in new_refs)
    state = candidate.current_state.model_copy(
        update={
            "opinion": opinion,
            "claims": (*claims, *retired_claims),
            "questions": (*questions, *retired_questions),
            "scenarios": scenarios,
            "risks": remap_factors(candidate.current_state.risks),
            "catalysts": remap_factors(candidate.current_state.catalysts),
            "invalidation_conditions": remap_factors(
                candidate.current_state.invalidation_conditions
            ),
            "evidence_refs": tuple(item.ref for item in combined_items),
        }
    )
    newly_observed_versions = tuple(
        item
        for version_id, item in candidate_versions.items()
        if version_id not in baseline_versions
    )
    change_signals = _change_signals(baseline, candidate.evidence_snapshot.source_records)
    source_domains, supports_quiet = _source_coverage(
        state,
        combined_versions,
        source_watermarks,
        status_blocking_records=newly_observed_versions,
        required_data_domains=tuple(
            dict.fromkeys(
                item.domain
                for item in candidate.coverage.domains
                if item.requirement is CoverageRequirement.REQUIRED
                and item.source is None
            )
        ),
    )
    if any(item.requires_full_analysis for item in change_signals):
        supports_quiet = False
    if question_disposition is not None and question_disposition.status == "limited":
        supports_quiet = False
    coverage_domains = (
        tuple(item for item in candidate.coverage.domains if item.source is None) + source_domains
    )

    confidence_rank = {
        ClaimConfidence.LOW: 0,
        ClaimConfidence.INDETERMINATE: 1,
        ClaimConfidence.MEDIUM: 2,
        ClaimConfidence.HIGH: 3,
    }
    claim_delta: list[ClaimRevisionDelta] = []
    for original in candidate.current_state.claims:
        previous = claim_matches.get(original.id)
        if previous is None:
            claim_delta.append(
                ClaimRevisionDelta(
                    object_id=original.id,
                    change=ClaimChange.INTRODUCED,
                    identity_disposition=(
                        IdentityDisposition.AMBIGUOUS_NEW
                        if original.id in ambiguous_claims
                        else IdentityDisposition.NEW
                    ),
                )
            )
            continue
        current = next(item for item in claims if item.id == previous.id)
        if current.standing is ClaimStanding.INVALIDATED:
            change = ClaimChange.INVALIDATED
        elif confidence_rank[current.confidence] > confidence_rank[previous.confidence]:
            change = ClaimChange.STRENGTHENED
        elif confidence_rank[current.confidence] < confidence_rank[previous.confidence]:
            change = ClaimChange.WEAKENED
        else:
            change = ClaimChange.REAFFIRMED
        claim_delta.append(
            ClaimRevisionDelta(
                object_id=current.id,
                previous_object_id=previous.id,
                change=change,
                identity_disposition=IdentityDisposition.EXACT_MATCH,
            )
        )
    claim_delta.extend(
        ClaimRevisionDelta(
            object_id=claim.id,
            previous_object_id=claim.id,
            change=ClaimChange.RETIRED,
            identity_disposition=IdentityDisposition.CONSERVATIVE_RETIREMENT,
        )
        for claim in retired_claims
    )

    question_delta: list[QuestionRevisionDelta] = []
    if question_disposition is not None and question_disposition.status == "complete":
        disposition_changes = {
            item.value: QuestionChange(item.value) for item in QuestionDispositionKind
        }
        question_delta.extend(
            QuestionRevisionDelta(
                object_id=item.baseline_question_id,
                previous_object_id=item.baseline_question_id,
                change=disposition_changes[item.disposition.value],
                identity_disposition=IdentityDisposition.EXACT_MATCH,
                evidence_refs=item.evidence_refs,
                reason=item.reason,
                successor_object_id=item.successor_question_id,
            )
            for item in question_disposition.dispositions
        )
        question_delta.extend(
            QuestionRevisionDelta(
                object_id=item.id,
                change=QuestionChange.INTRODUCED,
                identity_disposition=IdentityDisposition.NEW,
            )
            for item in candidate.current_state.questions
            if item.id
            not in {
                record.candidate_question_id
                for record in question_disposition.dispositions
                if record.candidate_question_id is not None
            }
        )
    else:
        for original in candidate.current_state.questions:
            previous = question_matches.get(original.id)
            question_delta.append(
                QuestionRevisionDelta(
                    object_id=question_ids.get(original.id, original.id),
                    previous_object_id=previous.id if previous is not None else None,
                    change=(
                        QuestionChange.REAFFIRMED
                        if previous is not None
                        else QuestionChange.INTRODUCED
                    ),
                    identity_disposition=(
                        IdentityDisposition.EXACT_MATCH
                        if previous is not None
                        else (
                            IdentityDisposition.AMBIGUOUS_NEW
                            if original.id in ambiguous_questions
                            else IdentityDisposition.NEW
                        )
                    ),
                )
            )
        question_delta.extend(
            QuestionRevisionDelta(
                object_id=question.id,
                previous_object_id=question.id,
                change=QuestionChange.REAFFIRMED,
                identity_disposition=IdentityDisposition.EXACT_MATCH,
            )
            for question in retired_questions
        )

    coverage_claims = tuple(
        item.model_copy(update={"object_id": claim_ids.get(item.object_id, item.object_id)})
        for item in candidate.coverage.claims
    ) + tuple(
        ResearchObjectCoverage(
            object_id=claim.id,
            status=CoverageStatus.LIMITED,
            evidence_refs=claim.evidence_refs,
            limitations=("The independent Full Analysis did not reproduce this Claim.",),
        )
        for claim in retired_claims
    )
    if question_disposition is not None and question_disposition.status == "complete":
        disposition_by_baseline = {
            item.baseline_question_id: item for item in question_disposition.dispositions
        }
        candidate_coverage_by_id = {item.object_id: item for item in candidate.coverage.questions}
        coverage_questions = tuple(
            ResearchObjectCoverage(
                object_id=question.id,
                status=(
                    CoverageStatus.COMPLETE
                    if question.id in disposition_by_baseline
                    else candidate_coverage_by_id.get(
                        question.id,
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    ).status
                ),
                evidence_refs=(
                    disposition_by_baseline[question.id].evidence_refs
                    if question.id in disposition_by_baseline
                    else candidate_coverage_by_id.get(
                        question.id,
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    ).evidence_refs
                ),
                limitations=(),
            )
            for question in questions
        )
    else:
        coverage_questions = tuple(
            item.model_copy(update={"object_id": question_ids.get(item.object_id, item.object_id)})
            for item in candidate.coverage.questions
        ) + tuple(
            ResearchObjectCoverage(
                object_id=question.id,
                status=CoverageStatus.LIMITED,
                evidence_refs=question.evidence_refs,
                limitations=("Question Disposition was not completed.",),
            )
            for question in retired_questions
        )
    if any(item.status is not CoverageStatus.COMPLETE for item in coverage_questions):
        supports_quiet = False
    opinion_changed = opinion.model_dump(
        exclude={"evidence_refs"}
    ) != baseline.current_state.opinion.model_dump(exclude={"evidence_refs"})

    def semantic_scenarios(values: tuple[ResearchScenarioState, ...]):
        return tuple(item.model_dump(exclude={"cutoff", "evidence_refs"}) for item in values)

    def semantic_factors(values: tuple[ResearchFactor, ...]):
        return tuple(item.model_dump(exclude={"evidence_refs"}) for item in values)

    changed_sections: list[str] = []
    if opinion_changed:
        changed_sections.append("opinion")
    if any(item.change is not ClaimChange.REAFFIRMED for item in claim_delta):
        changed_sections.append("claims")
    question_change_is_material = any(
        item.change is not QuestionChange.REAFFIRMED for item in question_delta
    )
    if question_disposition is not None and question_disposition.status == "limited":
        question_change_is_material = any(
            item.change is QuestionChange.INTRODUCED
            and item.object_id in independently_introduced_question_ids
            for item in question_delta
        )
    if question_change_is_material:
        changed_sections.append("questions")
    if semantic_scenarios(scenarios) != semantic_scenarios(baseline.current_state.scenarios):
        changed_sections.append("scenarios")
    for section, current_values, baseline_values in (
        ("risks", state.risks, baseline.current_state.risks),
        ("catalysts", state.catalysts, baseline.current_state.catalysts),
        (
            "invalidation_conditions",
            state.invalidation_conditions,
            baseline.current_state.invalidation_conditions,
        ),
    ):
        if semantic_factors(current_values) != semantic_factors(baseline_values):
            changed_sections.append(section)
    material = bool(changed_sections)
    material = material or any(
        item.kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING for item in change_signals
    )
    delta = RevisionDelta(
        opinion_changed=opinion_changed,
        claims=tuple(claim_delta),
        questions=tuple(question_delta),
        changed_sections=tuple(changed_sections),
        inherited_evidence_refs=inherited_refs,
        new_evidence_refs=tuple(item.ref for item in candidate_bundle.items),
        change_signals=change_signals,
        question_disposition=question_disposition,
    )
    change_conclusion = (
        ResearchChangeConclusion.MATERIAL_CHANGE
        if material
        else (
            ResearchChangeConclusion.NO_MATERIAL_CHANGE
            if supports_quiet
            else ResearchChangeConclusion.INDETERMINATE
        )
    )
    summary_values = {
        "baseline": baseline.cutoff.isoformat(),
        "cutoff": candidate.cutoff.isoformat(),
        "count": len(candidate_bundle.items),
        "outcome": change_conclusion.value,
    }
    summaries = {
        "en": (
            "Full Analysis as of {cutoff} was compared with the {baseline} "
            "current Research Chain head; {count} Evidence items were newly observed. "
            "Outcome: {outcome}."
        ),
        "zh-CN": (
            "截至 {cutoff} 的完整分析已与 {baseline} 当前研究链头部比较；"
            "本次新观察到 {count} 条证据。结果：{outcome}。"
        ),
        "ja": (
            "{cutoff} 時点のフル分析を {baseline} の現在の Research Chain head と比較し、"
            "{count} 件の新規 Evidence を確認しました。結果: {outcome}。"
        ),
    }
    summary_template = summaries.get(candidate.current_state.language)
    update_summary_text = (
        summary_template.format(**summary_values)
        if summary_template is not None
        else candidate.update_summary.summary
    )
    updated = candidate.model_copy(
        update={
            "execution_strategy": ResearchExecutionStrategy.FULL,
            "role": ResearchRevisionRole.UPDATE,
            "change_conclusion": change_conclusion,
            "indeterminate_reason": (
                (
                    IndeterminateReason.QUESTION_DISPOSITION_LIMITED
                    if question_disposition is not None and question_disposition.status == "limited"
                    else IndeterminateReason.COVERAGE_INCOMPLETE
                )
                if change_conclusion is ResearchChangeConclusion.INDETERMINATE
                else None
            ),
            "delta": delta,
            "current_state": state,
            "coverage": candidate.coverage.model_copy(
                update={
                    "claims": coverage_claims,
                    "questions": coverage_questions,
                    "domains": coverage_domains,
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *candidate.coverage.limitations,
                                *(value for item in source_domains for value in item.limitations),
                                *(
                                    (question_disposition.limitation_reason.value,)
                                    if question_disposition is not None
                                    and question_disposition.limitation_reason is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                    "supports_no_material_change": supports_quiet,
                }
            ),
            "update_summary": candidate.update_summary.model_copy(
                update={
                    "summary": update_summary_text,
                    "baseline_cutoff": baseline.cutoff,
                    "analysis_cutoff": candidate.cutoff,
                    "execution_strategy": ResearchExecutionStrategy.FULL,
                    "change_conclusion": change_conclusion,
                    "new_evidence_refs": tuple(item.ref for item in candidate_bundle.items),
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *candidate.update_summary.limitations,
                                *(value for item in source_domains for value in item.limitations),
                                *(
                                    (question_disposition.limitation_reason.value,)
                                    if question_disposition is not None
                                    and question_disposition.limitation_reason is not None
                                    else ()
                                ),
                                *(
                                    (
                                        "Legacy internal source dependencies were repaired.",
                                    )
                                    if dependency_compatibility_repaired
                                    else ()
                                ),
                            )
                        )
                    ),
                }
            ),
            "evidence_snapshot": EffectiveEvidenceSnapshot(
                bundle=combined_bundle,
                lineage=tuple(
                    EvidenceSnapshotItem(
                        evidence_ref=item.ref,
                        lineage="new" if item.ref in new_refs else "inherited",
                        source_revision_id=(None if item.ref in new_refs else baseline_revision_id),
                    )
                    for item in combined_items
                ),
                source_records=combined_versions,
                source_record_lineage=source_record_lineage,
                source_watermarks=source_watermarks,
            ),
        }
    )
    return ResearchRevisionDraft.model_validate(updated.model_dump(mode="python"))


def _current_state_market_reference_levels(
    levels: tuple[MarketReferenceLevel, ...],
    *,
    evidence: EvidenceBundle,
) -> tuple[tuple[MarketReferenceLevel, ...], bool]:
    """Keep audited Near-live levels in artifacts without moving the state cutoff."""

    evidence_by_ref = {item.ref: item for item in evidence.items}
    retained: list[MarketReferenceLevel] = []
    omitted_near_live = False
    for level in levels:
        if level.as_of_date <= evidence.analysis_date:
            retained.append(level)
            continue
        if level.temporal_basis is not NumericTemporalBasis.LIVE_SNAPSHOT:
            retained.append(level)
            continue
        referenced = tuple(evidence_by_ref.get(ref) for ref in level.date_evidence_refs)
        if not referenced or any(item is None for item in referenced):
            retained.append(level)
            continue
        retrieval_dates: list[date] = []
        admissible = True
        for item in referenced:
            assert item is not None
            if (
                item.quality is EvidenceQuality.UNAVAILABLE
                or item.provenance.get("evidence_admission", {}).get("status") == "withheld"
                or not item.origins
                or any(
                    origin.temporal_scope is not EvidenceTemporalScope.LIVE_ONLY
                    for origin in item.origins
                )
            ):
                admissible = False
                break
            for origin in item.origins:
                decision = evaluate_evidence_admission(
                    temporal_scope=origin.temporal_scope.value,
                    analysis_date=evidence.analysis_date,
                    instrument=evidence.instrument,
                    retrieved_at=origin.retrieved_at,
                    sealed_at=evidence.sealed_at,
                    effective_dates=tuple(
                        value
                        for value in (item.effective_date, origin.effective_date)
                        if value is not None
                    ),
                )
                if not decision.admitted or origin.retrieved_at is None:
                    admissible = False
                    break
                retrieved_at = datetime.fromisoformat(
                    origin.retrieved_at.replace("Z", "+00:00")
                )
                retrieval_dates.append(
                    retrieved_at.astimezone(market_timezone(evidence.instrument)).date()
                )
            if not admissible:
                break
        if not admissible or not retrieval_dates or max(retrieval_dates) != level.as_of_date:
            retained.append(level)
            continue
        omitted_near_live = True
    return tuple(retained), omitted_near_live


def assemble_full_revision(
    request: AnalysisRequest,
    execution: FullResearchExecution,
) -> ResearchRevisionDraft:
    """Assemble a complete initial revision after conclusion-independent research."""
    evidence = execution.evidence
    decision = execution.decision
    reports = execution.reports
    evidence_refs = tuple(item.ref for item in evidence.items)
    if not evidence_refs:
        raise ValueError("Research State Assembly requires sealed Evidence")
    if decision is None:
        raise ValueError("Research State Assembly requires a Research Decision")
    allowed_refs = set(evidence_refs)
    decision_refs = tuple(ref for ref in decision.evidence_refs if ref in allowed_refs)
    if not decision_refs:
        raise ValueError("Research Opinion requires explicit Evidence refs")
    language = report_language_value(request.output_language or "en")
    claims: list[ResearchClaim] = []

    for report in reports.values():
        for candidate in report.key_claims:
            refs = tuple(ref for ref in candidate.evidence_refs if ref in allowed_refs)
            if not refs:
                raise ValueError("Research Claims require explicit Evidence refs")
            _external_sources, internal_sources = partition_source_dependencies(
                candidate.required_sources
            )
            if internal_sources:
                raise ValueError(
                    "Research Claim required_sources contains an internal source reference"
                )
            available_sources = {
                source
                for item in evidence.items
                if item.ref in refs
                for source in (item.source, *(origin.source for origin in item.origins))
            }
            if not set(candidate.required_sources).issubset(available_sources):
                raise ValueError("Research Claim requires a source absent from cited Evidence")
            kind = EpistemicKind(candidate.kind.value)
            observed_dates = [
                item.effective_date or item.requested_date
                for item in evidence.items
                if item.ref in refs
            ]
            claims.append(
                ResearchClaim(
                    id=_new_claim_id(),
                    statement=candidate.statement,
                    epistemic_kind=kind,
                    decision_role=DecisionRole.THESIS,
                    confidence=_claim_confidence(candidate.confidence),
                    evidence_refs=refs,
                    required_sources=candidate.required_sources,
                    observed_at=(
                        max(observed_dates) if kind is EpistemicKind.OBSERVATION else None
                    ),
                    falsifier=(
                        None
                        if kind is EpistemicKind.OBSERVATION
                        else (
                            decision.invalidation_conditions[0]
                            if decision.invalidation_conditions
                            else f"Observable Evidence contradicts: {candidate.statement}"
                        )
                    ),
                )
            )
    if not claims:
        claims.append(
            ResearchClaim(
                id=_new_claim_id(),
                statement=decision.thesis,
                epistemic_kind=EpistemicKind.INFERENCE,
                decision_role=DecisionRole.THESIS,
                confidence=_claim_confidence(decision.confidence),
                evidence_refs=decision_refs,
                evidence_relationship="decision_envelope",
                falsifier=(
                    decision.invalidation_conditions[0]
                    if decision.invalidation_conditions
                    else "Observable Evidence contradicts the thesis."
                ),
            )
        )
    primary_claim_ids = tuple(claim.id for claim in claims)

    def factors(statements: tuple[str, ...], role: DecisionRole) -> tuple[ResearchFactor, ...]:
        output: list[ResearchFactor] = []
        for statement in statements:
            claim = ResearchClaim(
                id=_new_claim_id(),
                statement=statement,
                epistemic_kind=(
                    EpistemicKind.FORECAST
                    if role is DecisionRole.CATALYST
                    else EpistemicKind.INFERENCE
                ),
                decision_role=role,
                confidence=ClaimConfidence.INDETERMINATE,
                evidence_refs=decision_refs,
                evidence_relationship="decision_envelope",
                falsifier=f"Observable Evidence disproves: {statement}",
            )
            claims.append(claim)
            output.append(
                ResearchFactor(
                    statement=statement,
                    claim_ids=(claim.id,),
                    evidence_refs=decision_refs,
                )
            )
        return tuple(output)

    risks = factors(decision.risks, DecisionRole.RISK)
    catalysts = factors(decision.catalysts, DecisionRole.CATALYST)
    invalidations = factors(decision.invalidation_conditions, DecisionRole.INVALIDATION)
    scenarios: list[ResearchScenarioState] = []
    for scenario in decision.scenarios:
        scenario_refs = tuple(ref for ref in scenario.evidence_refs if ref in allowed_refs)
        if not scenario_refs:
            raise ValueError("Research Scenarios require explicit Evidence refs")
        assumption_ids = []
        for assumption in scenario.core_assumptions:
            claim = ResearchClaim(
                id=_new_claim_id(),
                statement=assumption,
                epistemic_kind=EpistemicKind.INFERENCE,
                decision_role=DecisionRole.SCENARIO_ASSUMPTION,
                confidence=ClaimConfidence.INDETERMINATE,
                evidence_refs=scenario_refs,
                falsifier=f"Observable Evidence disproves: {assumption}",
            )
            claims.append(claim)
            assumption_ids.append(claim.id)
        scenarios.append(
            ResearchScenarioState(
                kind=scenario.kind,
                likelihood=ScenarioLikelihood.INDETERMINATE,
                cutoff=request.analysis_date,
                horizon=decision.time_horizon,
                outcome=scenario.outcome,
                assumption_claim_ids=tuple(assumption_ids),
                evidence_refs=scenario_refs,
            )
        )
    question_dependencies = {
        item.question: item.required_sources for item in decision.question_source_dependencies
    }
    if any(
        partition_source_dependencies(required_sources)[1]
        for required_sources in question_dependencies.values()
    ):
        raise ValueError(
            "Research Question required_sources contains an internal source reference"
        )
    questions = tuple(
        ResearchQuestion(
            id=_new_question_id(),
            question=question,
            status=QuestionStatus.OPEN,
            required_sources=question_dependencies.get(question, ()),
        )
        for question in decision.unresolved_questions
    )
    state_market_reference_levels, omitted_near_live_reference = (
        _current_state_market_reference_levels(
            decision.market_reference_levels,
            evidence=evidence,
        )
    )
    state = CurrentResearchState(
        language=language,
        instrument=request.ticker,
        cutoff=request.analysis_date,
        opinion=ResearchOpinion(
            rating=decision.rating,
            confidence=_decision_confidence(decision.confidence),
            thesis=decision.thesis,
            primary_claim_ids=primary_claim_ids,
            evidence_refs=decision_refs,
        ),
        claims=tuple(claims),
        questions=questions,
        scenarios=tuple(scenarios),
        risks=risks,
        catalysts=catalysts,
        invalidation_conditions=invalidations,
        market_reference_levels=state_market_reference_levels,
        evidence_refs=evidence_refs,
    )
    source_records, source_watermarks = _source_metadata(evidence)
    source_domains, supports_quiet = _source_coverage(
        state,
        source_records,
        source_watermarks,
        required_data_domains=request.analysts,
    )
    domains: list[ResearchDomainCoverage] = []
    limitations: list[str] = (
        [_NEAR_LIVE_MARKET_REFERENCE_LIMITATION]
        if omitted_near_live_reference
        else []
    )
    for analyst in request.analysts:
        report = reports.get(analyst)
        complete = bool(
            report is not None
            and getattr(report.audit_status, "value", report.audit_status) == "complete"
        )
        domain_limitations = () if complete else (f"{analyst} audit incomplete",)
        limitations.extend(domain_limitations)
        domains.append(
            ResearchDomainCoverage(
                domain=analyst,
                requirement=(
                    CoverageRequirement.ADVISORY
                    if analyst in {"social", "news"}
                    else CoverageRequirement.REQUIRED
                ),
                status=(CoverageStatus.COMPLETE if complete else CoverageStatus.LIMITED),
                evidence_refs=(
                    tuple(ref for ref in report.source_refs if ref in allowed_refs)
                    if report is not None
                    else ()
                ),
                limitations=domain_limitations,
            )
        )
    domains.extend(source_domains)
    for domain in source_domains:
        limitations.extend(domain.limitations)
    complete_domain_refs = {
        ref
        for domain in domains
        if domain.status is CoverageStatus.COMPLETE
        for ref in domain.evidence_refs
    }
    claim_coverage: list[ResearchObjectCoverage] = []
    for claim in claims:
        direct = claim.evidence_relationship == "direct"
        complete = direct and set(claim.evidence_refs).issubset(complete_domain_refs)
        limitation = (
            "Granular Evidence relationship is unavailable; only the final "
            "decision Evidence envelope was recorded."
            if not direct
            else "One or more supporting domains have limited coverage."
        )
        claim_limitations = () if complete else (limitation,)
        limitations.extend(claim_limitations)
        claim_coverage.append(
            ResearchObjectCoverage(
                object_id=claim.id,
                status=(CoverageStatus.COMPLETE if complete else CoverageStatus.LIMITED),
                evidence_refs=claim.evidence_refs,
                limitations=claim_limitations,
            )
        )
    question_coverage = tuple(
        ResearchObjectCoverage(
            object_id=question.id,
            status=CoverageStatus.LIMITED,
            limitations=("Question remains open without answering Evidence.",),
        )
        for question in questions
    )
    if question_coverage:
        limitations.append("One or more Research Questions remain open.")
    summaries = {
        "en": "Initial Full Analysis established the first Current Research State.",
        "zh-CN": "首次完整分析已建立第一版当前研究状态。",
        "ja": "初回のフル分析で最初の現在研究状態を確立しました。",
    }
    return ResearchRevisionDraft(
        cutoff=request.analysis_date,
        role=ResearchRevisionRole.INITIAL,
        execution_strategy=ResearchExecutionStrategy.FULL,
        change_conclusion=None,
        delta=RevisionDelta(
            opinion_changed=True,
            claims=tuple(
                ClaimRevisionDelta(
                    object_id=claim.id,
                    change=ClaimChange.INTRODUCED,
                    identity_disposition=IdentityDisposition.NEW,
                )
                for claim in claims
            ),
            questions=tuple(
                QuestionRevisionDelta(
                    object_id=question.id,
                    change=QuestionChange.INTRODUCED,
                    identity_disposition=IdentityDisposition.NEW,
                )
                for question in questions
            ),
            new_evidence_refs=evidence_refs,
        ),
        current_state=state,
        coverage=CoverageAttestation(
            claims=tuple(claim_coverage),
            questions=question_coverage,
            domains=tuple(domains),
            limitations=tuple(dict.fromkeys(limitations)),
            supports_no_material_change=supports_quiet,
        ),
        update_summary=UpdateSummary(
            language=language,
            summary=summaries.get(language, decision.executive_summary),
            checked_domains=tuple(item.domain for item in domains),
            limitations=tuple(dict.fromkeys(limitations)),
            analysis_cutoff=request.analysis_date,
            execution_strategy=ResearchExecutionStrategy.FULL,
            change_conclusion=None,
            new_evidence_refs=evidence_refs,
        ),
        evidence_snapshot=EffectiveEvidenceSnapshot(
            bundle=evidence,
            lineage=tuple(
                EvidenceSnapshotItem(evidence_ref=ref, lineage="new") for ref in evidence_refs
            ),
            source_records=source_records,
            source_record_lineage=tuple(
                SourceRecordSnapshotItem(
                    version_id=record.version_id,
                    lineage="new",
                    observed_in_execution=True,
                )
                for record in source_records
            ),
            source_watermarks=source_watermarks,
        ),
    )
