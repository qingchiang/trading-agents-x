"""Research capability, coverage, and next-update policy."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
from typing import Literal

from tradingagents.dataflows.jp.calendar import is_tse_open
from tradingagents.dataflows.symbol_utils import is_supported_equity_symbol, market_timezone

from ._research_capabilities import market_research_capability_profile
from ._research_coverage import RequiredSourceCoverageIndex
from ._research_models import (
    AnchorQualificationReason,
    CapabilityAttestation,
    ClaimChange,
    ClaimStanding,
    CoverageRequirement,
    CoverageStatus,
    CurrentResearchState,
    EffectiveEvidenceSnapshot,
    ForwardResearchAnchorQualification,
    IdentityDisposition,
    IncrementalEscalationReason,
    MarketResearchCapability,
    MarketResearchCapabilityProfile,
    NextUpdateReason,
    QuestionChange,
    QuestionStatus,
    ResearchChain,
    ResearchChangeConclusion,
    ResearchChangeKind,
    ResearchClaim,
    ResearchExecutionStrategy,
    ResearchModel,
    ResearchQuestion,
    ResearchRevisionDraft,
    SourceCoverageLimitation,
    SourceDependencyCompatibilityLimitation,
    SourceObservationInterval,
    SourceRecordStatus,
    SourceRecordVersion,
    SourceWatermarkSnapshot,
    TransitionContinuityRule,
    TransitionCoverageAttestation,
    legacy_forward_research_anchor_qualification,
)
from .contracts import EvidenceBundle, ResearchUpdateAudit, ResearchUpdateTransitionCoverage
from .research_intervals import DateIntervalSet
from .source_dependencies import partition_source_dependencies


def _calendar_gaps(
    start: date,
    end: date,
    observed: tuple[SourceObservationInterval, ...],
) -> tuple[SourceObservationInterval, ...]:
    return tuple(
        SourceObservationInterval(start=item.start, end=item.end)
        for item in DateIntervalSet(observed).gaps(start, end)
    )


def _transition_calendar_start(frontier: datetime, market_tz: tzinfo) -> date:
    local_frontier = frontier.astimezone(market_tz)
    return local_frontier.date() + (
        timedelta(days=1) if local_frontier.time() == time.max else timedelta()
    )


def _continuity_gaps(
    rule: TransitionContinuityRule,
    transition_start: date,
    transition_end: date,
    checked: tuple[SourceObservationInterval, ...],
) -> tuple[SourceObservationInterval, ...]:
    if rule is TransitionContinuityRule.EVENT_STREAM:
        return _calendar_gaps(transition_start, transition_end, checked)
    required_date = (
        _latest_permitted_market_session(transition_end)
        if rule is TransitionContinuityRule.MARKET_SERIES
        else transition_end
    )
    if DateIntervalSet(checked).covers(required_date):
        return ()
    return (SourceObservationInterval(start=required_date, end=required_date),)

def _semantic_state_payload(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _semantic_state_payload(item)
            for key, item in value.items()
            if key not in {"cutoff", "evidence_refs"}
        }
    if isinstance(value, (list, tuple)):
        return tuple(_semantic_state_payload(item) for item in value)
    return value


def validate_experimental_nmc_candidate(
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
) -> IncrementalEscalationReason | None:
    """Fail closed unless a bounded candidate can safely become authoritative."""
    if (
        evaluate_next_update_policy(
            baseline,
            instrument=baseline.current_state.instrument,
            mode="experimental",
        ).policy
        != "incremental_allowed"
    ):
        return IncrementalEscalationReason.INVALID_BASELINE
    if (
        candidate.execution_strategy is not ResearchExecutionStrategy.INCREMENTAL
        or candidate.change_conclusion is not ResearchChangeConclusion.NO_MATERIAL_CHANGE
        or candidate.cutoff <= baseline.cutoff
        or candidate.current_state.instrument != baseline.current_state.instrument
        or candidate.current_state.cutoff != candidate.cutoff
        or candidate.evidence_snapshot.bundle.analysis_date != candidate.cutoff
    ):
        return IncrementalEscalationReason.SCHEMA_INVALID
    status_reasons = {
        SourceRecordStatus.CORRECTED: IncrementalEscalationReason.SOURCE_CORRECTION,
        SourceRecordStatus.WITHDRAWN: IncrementalEscalationReason.SOURCE_WITHDRAWAL,
        SourceRecordStatus.REPLACED: IncrementalEscalationReason.SOURCE_REPLACEMENT,
    }
    source_lineage = {
        item.version_id: item for item in candidate.evidence_snapshot.source_record_lineage
    }
    for record in candidate.evidence_snapshot.source_records:
        if record.status in status_reasons and source_lineage[record.version_id].lineage == "new":
            return status_reasons[record.status]
    for signal in candidate.delta.change_signals:
        if not signal.requires_full_analysis:
            continue
        if signal.kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING:
            return IncrementalEscalationReason.THRESHOLD_CROSSING
        if signal.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY:
            return IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
        if signal.kind is ResearchChangeKind.FUNDAMENTAL_CORRECTION:
            return IncrementalEscalationReason.SOURCE_CORRECTION
        return IncrementalEscalationReason.SOURCE_VERSION_CHANGE
    if (
        not candidate.coverage.supports_no_material_change
        or any(
            item.requirement is CoverageRequirement.REQUIRED
            and item.status is not CoverageStatus.COMPLETE
            for item in candidate.coverage.domains
        )
        or any(item.status is not CoverageStatus.COMPLETE for item in candidate.coverage.claims)
        or any(item.status is not CoverageStatus.COMPLETE for item in candidate.coverage.questions)
    ):
        return IncrementalEscalationReason.COVERAGE_INCOMPLETE
    if _semantic_state_payload(candidate.current_state.model_dump(mode="python")) != (
        _semantic_state_payload(baseline.current_state.model_dump(mode="python"))
    ):
        return IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
    active_claim_ids = {
        item.id for item in baseline.current_state.claims if item.standing is ClaimStanding.ACTIVE
    }
    reaffirmed_claim_ids = {
        item.object_id
        for item in candidate.delta.claims
        if item.change is ClaimChange.REAFFIRMED
        and item.identity_disposition is IdentityDisposition.EXACT_MATCH
        and item.previous_object_id == item.object_id
    }
    current_question_ids = {
        item.id
        for item in baseline.current_state.questions
        if item.status in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}
    }
    reaffirmed_question_ids = {
        item.object_id
        for item in candidate.delta.questions
        if item.change is QuestionChange.REAFFIRMED
        and item.identity_disposition is IdentityDisposition.EXACT_MATCH
        and item.previous_object_id == item.object_id
    }
    if (
        candidate.delta.opinion_changed
        or candidate.delta.changed_sections
        or reaffirmed_claim_ids != active_claim_ids
        or reaffirmed_question_ids != current_question_ids
    ):
        return IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
    if not _required_source_coverage_complete(
        candidate,
        required_incremental_sources(candidate),
    ):
        return IncrementalEscalationReason.COVERAGE_INCOMPLETE
    return None


def prepare_experimental_nmc_revision(
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
    audit: ResearchUpdateAudit,
) -> ResearchRevisionDraft:
    """Render deterministic NMC summary text and attach the experiment audit."""
    values = {
        "baseline": baseline.cutoff.isoformat(),
        "cutoff": candidate.cutoff.isoformat(),
        "count": len(candidate.delta.new_evidence_refs),
    }
    summaries = {
        "en": (
            "Bounded assessment from {baseline} to {cutoff} found no material change; "
            "the Current Research State was reaffirmed with {count} newly observed "
            "Evidence items."
        ),
        "zh-CN": (
            "从 {baseline} 到 {cutoff} 的有限变化评估未发现重大变化；当前研究状态已重申，"
            "并记录 {count} 条新观察到的证据。"
        ),
        "ja": (
            "{baseline} から {cutoff} までの限定的な変更評価では重要な変更は確認されず、"
            "新たに観測した {count} 件の Evidence とともに現在の Research State を再確認しました。"
        ),
    }
    summary = summaries.get(candidate.current_state.language, summaries["en"]).format(**values)
    updated = candidate.model_copy(
        update={
            "update_summary": candidate.update_summary.model_copy(update={"summary": summary}),
            "research_update_audit": audit,
        }
    )
    return ResearchRevisionDraft.model_validate(updated.model_dump(mode="python"))


def close_revision_over_update_candidate(
    revision: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft | EffectiveEvidenceSnapshot,
) -> ResearchRevisionDraft:
    """Seal bounded candidate Evidence into an authoritative Full Revision snapshot."""
    full_snapshot = revision.evidence_snapshot
    candidate_snapshot = (
        candidate.evidence_snapshot if isinstance(candidate, ResearchRevisionDraft) else candidate
    )
    items = tuple(
        {
            item.ref: item
            for item in (*full_snapshot.bundle.items, *candidate_snapshot.bundle.items)
        }.values()
    )
    tables = tuple(
        {
            item.id: item
            for item in (*full_snapshot.bundle.tables, *candidate_snapshot.bundle.tables)
        }.values()
    )
    lineage = {
        item.evidence_ref: item for item in (*candidate_snapshot.lineage, *full_snapshot.lineage)
    }
    records = tuple(
        {
            item.version_id: item
            for item in (*full_snapshot.source_records, *candidate_snapshot.source_records)
        }.values()
    )
    source_lineage = {
        item.version_id: item
        for item in (
            *candidate_snapshot.source_record_lineage,
            *full_snapshot.source_record_lineage,
        )
    }
    snapshot = EffectiveEvidenceSnapshot(
        bundle=EvidenceBundle(
            instrument=full_snapshot.bundle.instrument,
            analysis_date=full_snapshot.bundle.analysis_date,
            items=items,
            tables=tables,
            sealed_at=full_snapshot.bundle.sealed_at,
        ),
        lineage=tuple(lineage[item.ref] for item in items),
        source_records=records,
        source_record_lineage=tuple(source_lineage[item.version_id] for item in records),
        source_watermarks=tuple(
            {
                (item.source, item.scanned_start, item.scanned_end): item
                for item in (
                    *candidate_snapshot.source_watermarks,
                    *full_snapshot.source_watermarks,
                )
            }.values()
        ),
    )
    return revision.model_copy(update={"evidence_snapshot": snapshot})


def _anchor_watermark_usable(
    watermark: SourceWatermarkSnapshot,
    revision: ResearchRevisionDraft,
) -> bool:
    if (
        watermark.status is CoverageStatus.UNAVAILABLE
        or watermark.temporal_scope != "point_in_time"
        or watermark.information_frontier is None
        or revision.information_frontier is None
        or watermark.information_frontier > revision.information_frontier
        or watermark.scanned_end != revision.cutoff
        or (
            watermark.reported_records is not None
            and watermark.reported_records < watermark.returned_records
        )
    ):
        return False
    blocking = tuple(
        limitation
        for limitation in watermark.structured_limitations
        if not (
            limitation.kind == "archive_truncation"
            and limitation.temporal_scope == "point_in_time"
            and limitation.observed_intervals
            and max(item.end for item in limitation.observed_intervals) == revision.cutoff
        )
    )
    return not blocking and (
        watermark.status is CoverageStatus.COMPLETE
        or (
            watermark.status is CoverageStatus.LIMITED
            and watermark.structured_limitations
            and not blocking
        )
    )


def _latest_permitted_market_session(cutoff: date) -> date:
    session = cutoff
    while not is_tse_open(session):
        session -= timedelta(days=1)
    return session


def _source_record_published_date(record: SourceRecordVersion) -> date | None:
    try:
        return date.fromisoformat(record.published_at[:10])
    except ValueError:
        return None


def _anchor_record_point_in_time_valid(
    record: SourceRecordVersion,
    revision: ResearchRevisionDraft,
) -> bool:
    return revision.information_frontier is not None and (
        record.available_at <= revision.information_frontier
        and record.available_at.astimezone(
            market_timezone(revision.current_state.instrument)
        ).date()
        <= revision.cutoff
    )


def transition_coverage_is_complete(
    revision: ResearchRevisionDraft,
    transition_coverage: TransitionCoverageAttestation | ResearchUpdateTransitionCoverage | None,
    *,
    anchor_frontier: datetime,
    update_frontier: datetime,
) -> bool:
    """Validate the persisted aggregate against its required capability details."""
    if transition_coverage is None:
        return False
    capabilities = transition_coverage.capabilities
    required_capabilities = {
        item.capability.value
        for item in (
            revision.coverage.anchor_qualification.capabilities
            if revision.coverage.anchor_qualification is not None
            else ()
        )
        if item.required
    }
    if not required_capabilities:
        profile = market_research_capability_profile(revision.current_state.instrument)
        required_capabilities = {
            item.value for item in (profile.minimum_anchor_capabilities if profile else ())
        }
    by_capability = {
        getattr(item.capability, "value", item.capability): item for item in capabilities
    }
    profile = market_research_capability_profile(revision.current_state.instrument)
    if (
        not transition_coverage.complete
        or transition_coverage.anchor_frontier != anchor_frontier
        or transition_coverage.update_frontier != update_frontier
        or len(by_capability) != len(capabilities)
        or not required_capabilities
        or not required_capabilities.issubset(by_capability)
        or profile is None
    ):
        return False
    transition_start = _transition_calendar_start(
        anchor_frontier,
        market_timezone(revision.current_state.instrument),
    )
    transition_end = update_frontier.astimezone(
        market_timezone(revision.current_state.instrument)
    ).date()
    contracts = {item.capability.value: item for item in profile.source_contracts}
    return all(
        by_capability[capability].required for capability in required_capabilities
    ) and all(
        not item.required
        or (
            item.complete
            and bool(item.checked_intervals)
            and not item.gaps
            and not any(limitation.scope == "transition" for limitation in item.limitations)
            and (contract := contracts.get(getattr(item.capability, "value", item.capability)))
            is not None
            and any(
                set(source_set).issubset(item.sources)
                for source_set in contract.acceptable_source_sets
            )
            and not _continuity_gaps(
                contract.transition_continuity,
                transition_start,
                transition_end,
                item.checked_intervals,
            )
        )
        for item in capabilities
    )


def _authoritative_incremental_nmc_reaffirms_full_anchor(
    revision: ResearchRevisionDraft,
) -> bool:
    audit = revision.research_update_audit
    return (
        revision.execution_strategy is ResearchExecutionStrategy.INCREMENTAL
        and revision.change_conclusion is ResearchChangeConclusion.NO_MATERIAL_CHANGE
        and audit is not None
        and audit.mode == "experimental"
        and audit.authoritative_strategy == "incremental"
        and audit.candidate is not None
        and audit.candidate.change_conclusion == "no_material_change"
        and revision.information_frontier is not None
        and audit.baseline_information_frontier is not None
        and audit.transition_coverage is not None
        and transition_coverage_is_complete(
            revision,
            audit.transition_coverage,
            anchor_frontier=audit.baseline_information_frontier,
            update_frontier=revision.information_frontier,
        )
        and audit.escalation_reason is None
    )


def derive_forward_research_anchor(
    revision: ResearchRevisionDraft,
) -> ForwardResearchAnchorQualification:
    """Derive future comparison eligibility from Full-established research quality."""
    existing = revision.coverage.anchor_qualification
    if (
        existing is not None
        and AnchorQualificationReason.ANCHOR_READINESS_NOT_REQUIRED in existing.reasons
    ):
        return existing
    profile = market_research_capability_profile(revision.current_state.instrument)
    reasons: list[AnchorQualificationReason] = []
    if profile is None or not profile.bounded_execution_supported:
        reasons.append(AnchorQualificationReason.UNSUPPORTED_MARKET_PROFILE)
        return ForwardResearchAnchorQualification(
            is_forward_research_anchor=False,
            reasons=tuple(reasons),
        )
    if (
        revision.execution_strategy is not ResearchExecutionStrategy.FULL
        and not _authoritative_incremental_nmc_reaffirms_full_anchor(revision)
    ):
        reasons.append(AnchorQualificationReason.NOT_FULL_RESEARCH)
    if revision.information_frontier is None:
        reasons.append(AnchorQualificationReason.INFORMATION_FRONTIER_MISSING)
    if any(
        not _anchor_record_point_in_time_valid(record, revision)
        for record in revision.evidence_snapshot.source_records
    ):
        reasons.append(AnchorQualificationReason.POINT_IN_TIME_INVALID)

    required_capabilities = _required_research_capabilities(revision, profile)

    watermarks_by_source: dict[str, tuple[SourceWatermarkSnapshot, ...]] = {
        source: tuple(
            item for item in revision.evidence_snapshot.source_watermarks if item.source == source
        )
        for source in {
            source
            for contract in profile.source_contracts
            for source_set in contract.acceptable_source_sets
            for source in source_set
        }
    }
    records_by_source = {
        source: tuple(
            item
            for item in revision.evidence_snapshot.source_records
            if item.source == source and _anchor_record_point_in_time_valid(item, revision)
        )
        for source in watermarks_by_source
    }
    attestations: list[CapabilityAttestation] = []
    for contract in profile.source_contracts:
        capability = contract.capability
        required = capability in required_capabilities

        def source_attests(
            source: str,
            capability: MarketResearchCapability = capability,
        ) -> bool:
            watermarks = watermarks_by_source[source]
            records = records_by_source[source]
            latest_market_session = _latest_permitted_market_session(revision.cutoff)
            market_observation_matches_cutoff = (
                capability is not MarketResearchCapability.MARKET_OBSERVATION
                or any(
                    _source_record_published_date(item) == latest_market_session for item in records
                )
            )
            return (
                bool(watermarks)
                and all(item.scanned_end <= revision.cutoff for item in watermarks)
                and any(_anchor_watermark_usable(item, revision) for item in watermarks)
                and (
                    (
                        capability is not MarketResearchCapability.MARKET_OBSERVATION
                        and all(item.returned_records == 0 for item in watermarks)
                    )
                    or bool(records)
                )
                and market_observation_matches_cutoff
            )

        satisfied_sources = next(
            (
                source_set
                for source_set in contract.acceptable_source_sets
                if all(source_attests(source) for source in source_set)
            ),
            (),
        )
        satisfied = bool(satisfied_sources)
        attestations.append(
            CapabilityAttestation(
                capability=contract.capability,
                required=required,
                satisfied=satisfied,
                sources=satisfied_sources,
                limitations=(
                    ()
                    if satisfied or not required
                    else ("No acceptable configured source set attested the capability.",)
                ),
            )
        )
        if required and not satisfied:
            reasons.append(AnchorQualificationReason.REQUIRED_CAPABILITY_MISSING)

    required_domains = tuple(
        item
        for item in revision.coverage.domains
        if item.requirement is CoverageRequirement.REQUIRED
    )
    if any(
        item.status is not CoverageStatus.COMPLETE
        and not (
            item.source in watermarks_by_source
            and any(
                _anchor_watermark_usable(mark, revision)
                for mark in watermarks_by_source[item.source]
            )
        )
        for item in required_domains
    ):
        reasons.append(AnchorQualificationReason.REQUIRED_DOMAIN_MISSING)
    object_coverage = {
        item.object_id: item for item in (*revision.coverage.claims, *revision.coverage.questions)
    }
    for research_object in (*revision.current_state.claims, *revision.current_state.questions):
        if (
            isinstance(research_object, ResearchClaim)
            and research_object.standing is not ClaimStanding.ACTIVE
        ) or (
            isinstance(research_object, ResearchQuestion)
            and research_object.status is not QuestionStatus.OPEN
        ):
            continue
        coverage = object_coverage[research_object.id]
        if coverage.status is CoverageStatus.COMPLETE:
            continue
        if (
            isinstance(research_object, ResearchQuestion)
            and coverage.status is CoverageStatus.UNAVAILABLE
        ):
            reasons.append(AnchorQualificationReason.REQUIRED_DOMAIN_MISSING)
            break
        required_sources = set(research_object.required_sources)
        checked_sources = {
            domain.source
            for domain in required_domains
            if domain.status is CoverageStatus.COMPLETE and domain.source is not None
        }
        if required_sources - checked_sources:
            reasons.append(AnchorQualificationReason.REQUIRED_DOMAIN_MISSING)
            break
        if isinstance(research_object, ResearchClaim) and not (
            coverage.status is CoverageStatus.LIMITED
            and research_object.evidence_relationship == "decision_envelope"
        ):
            reasons.append(AnchorQualificationReason.CURRENT_STATE_UNUSABLE)
            break
    if any(
        item.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY
        for item in revision.delta.change_signals
    ):
        reasons.append(AnchorQualificationReason.INCOMPATIBLE_MARKET_SEMANTICS)
    if any(
        domain.requirement is CoverageRequirement.REQUIRED
        and domain.domain in {"market", "fundamentals"}
        and "audit incomplete" in " ".join(domain.limitations).lower()
        for domain in revision.coverage.domains
    ):
        reasons.append(AnchorQualificationReason.FULL_AUDIT_INCOMPLETE)
    promoted_audit_domains = {
        MarketResearchCapability.MEDIA: "news",
        MarketResearchCapability.SOCIAL_SENTIMENT: "social",
        MarketResearchCapability.MACRO: "macro",
    }
    if any(
        attestation.required
        and (audit_domain := promoted_audit_domains.get(attestation.capability)) is not None
        and not any(
            domain.domain == audit_domain and domain.status is CoverageStatus.COMPLETE
            for domain in revision.coverage.domains
        )
        for attestation in attestations
    ):
        reasons.append(AnchorQualificationReason.FULL_AUDIT_INCOMPLETE)
    return ForwardResearchAnchorQualification(
        is_forward_research_anchor=not reasons,
        profile_id=profile.id,
        reasons=tuple(dict.fromkeys(reasons)),
        capabilities=tuple(attestations),
    )


def bind_information_frontier(
    revision: ResearchRevisionDraft,
    frontier: datetime,
) -> ResearchRevisionDraft:
    """Bind one frozen execution frontier to its Revision and source attestations."""
    if frontier.utcoffset() is None:
        raise ValueError("Information Frontier requires a timezone")
    market_tz = market_timezone(revision.current_state.instrument)
    watermarks = []
    for watermark in revision.evidence_snapshot.source_watermarks:
        requested = watermark.requested_interval or SourceObservationInterval(
            start=watermark.scanned_start,
            end=watermark.scanned_end,
        )
        observed = watermark.observed_intervals or (
            ()
            if watermark.status is CoverageStatus.UNAVAILABLE
            else (
                SourceObservationInterval(
                    start=watermark.scanned_start,
                    end=watermark.scanned_end,
                ),
            )
        )
        attested_through = (
            None
            if watermark.status is CoverageStatus.UNAVAILABLE or not observed
            else min(
                frontier,
                datetime.combine(max(item.end for item in observed), time.max, tzinfo=market_tz),
            )
        )
        kind = (
            "live_only"
            if watermark.temporal_scope == "live_only"
            else "unknown"
            if watermark.temporal_scope == "unknown"
            else "unavailable"
            if watermark.status is CoverageStatus.UNAVAILABLE
            else "partial"
        )
        structured = watermark.structured_limitations or tuple(
            SourceCoverageLimitation(
                kind=kind,
                temporal_scope=watermark.temporal_scope,
                requested_interval=requested,
                observed_intervals=observed,
                presentation_text=text,
            )
            for text in watermark.limitations
        )
        watermarks.append(
            watermark.model_copy(
                update={
                    "information_frontier": attested_through,
                    "requested_interval": requested,
                    "observed_intervals": observed,
                    "structured_limitations": structured,
                }
            )
        )
    bundle = EvidenceBundle.model_validate(
        {
            **revision.evidence_snapshot.bundle.model_dump(mode="python"),
            "information_frontier": frontier,
            "digest": None,
        }
    )
    snapshot = revision.evidence_snapshot.model_copy(
        update={
            "bundle": bundle,
            "source_watermarks": tuple(watermarks),
        }
    )
    bound = revision.model_copy(
        update={"information_frontier": frontier, "evidence_snapshot": snapshot}
    )
    bound = bound.model_copy(
        update={
            "coverage": bound.coverage.model_copy(
                update={
                    "schema_version": "2",
                    "anchor_qualification": derive_forward_research_anchor(bound),
                }
            )
        }
    )
    return bound


class NextUpdatePolicyEvaluation(ResearchModel):
    """One typed decision shared by presentation, enforcement, and execution."""

    policy: Literal["incremental_allowed", "full_required"]
    reason: NextUpdateReason | None = None
    instrument: str
    mode: Literal["off", "shadow", "experimental"]
    required_sources: tuple[str, ...] = ()
    compatibility_limitations: tuple[SourceDependencyCompatibilityLimitation, ...] = ()


def required_research_sources(state: CurrentResearchState) -> tuple[str, ...]:
    """Return source dependencies declared by active Claims and open Questions."""
    sources = {
        source
        for claim in state.claims
        if claim.standing is ClaimStanding.ACTIVE
        for source in claim.required_sources
    }
    sources.update(
        source
        for question in state.questions
        if question.status is QuestionStatus.OPEN
        for source in question.required_sources
    )
    return tuple(sorted(sources))


def _all_research_source_dependencies(state: CurrentResearchState) -> tuple[str, ...]:
    """Return persisted dependencies from active and inactive research objects."""

    return tuple(
        dict.fromkeys(
            source
            for item in (*state.claims, *state.questions)
            for source in item.required_sources
        )
    )


def _required_research_capabilities(
    revision: ResearchRevisionDraft,
    profile: MarketResearchCapabilityProfile,
) -> set[MarketResearchCapability]:
    required = set(profile.minimum_anchor_capabilities)
    if any(
        domain.domain == "fundamentals" and domain.requirement is CoverageRequirement.REQUIRED
        for domain in revision.coverage.domains
    ):
        required.add(MarketResearchCapability.FUNDAMENTALS)
    explicitly_required = set(required_research_sources(revision.current_state))
    for contract in profile.source_contracts:
        if any(
            source in explicitly_required
            for source_set in contract.acceptable_source_sets
            for source in source_set
        ):
            required.add(contract.capability)
    return required


def required_incremental_sources(revision: ResearchRevisionDraft) -> tuple[str, ...]:
    external_sources, _internal_sources = partition_source_dependencies(
        required_research_sources(revision.current_state)
    )
    sources = set(external_sources)
    profile = market_research_capability_profile(revision.current_state.instrument)
    if profile is not None:
        required_capabilities = _required_research_capabilities(revision, profile)
        qualified_sources = {
            item.capability: item.sources
            for item in (
                revision.coverage.anchor_qualification.capabilities
                if revision.coverage.anchor_qualification is not None
                else ()
            )
            if item.required and item.satisfied and item.sources
        }
        for contract in profile.source_contracts:
            if contract.capability not in required_capabilities:
                continue
            sources.update(
                qualified_sources.get(
                    contract.capability,
                    contract.acceptable_source_sets[0],
                )
            )
    for domain in revision.coverage.domains:
        if domain.requirement is not CoverageRequirement.REQUIRED:
            continue
        if domain.source:
            sources.add(domain.source)
    admitted_sources, _internal_sources = partition_source_dependencies(sources)
    return tuple(sorted(admitted_sources))


def _required_source_coverage_complete(
    revision: ResearchRevisionDraft,
    required_sources: tuple[str, ...],
) -> bool:
    return RequiredSourceCoverageIndex.build(revision).complete(required_sources)


def _forward_anchor_policy_reason(
    revision: ResearchRevisionDraft,
) -> NextUpdateReason | None:
    if revision.coverage.anchor_qualification is None:
        return NextUpdateReason.LEGACY_ANCHOR_COVERAGE_UNPROVEN
    qualification = derive_forward_research_anchor(revision)
    if qualification.is_forward_research_anchor:
        return None
    if AnchorQualificationReason.INCOMPATIBLE_MARKET_SEMANTICS in qualification.reasons:
        return NextUpdateReason.INCOMPATIBLE_MARKET_SEMANTICS
    return NextUpdateReason.ANCHOR_COVERAGE_INCOMPLETE


def evaluate_next_update_policy(
    revision: ResearchRevisionDraft,
    *,
    instrument: str,
    mode: Literal["off", "shadow", "experimental"],
) -> NextUpdatePolicyEvaluation:
    """Evaluate the complete fail-closed bounded-update capability."""

    _external_dependencies, internal_dependencies = partition_source_dependencies(
        _all_research_source_dependencies(revision.current_state)
    )
    compatibility_limitations = (
        (SourceDependencyCompatibilityLimitation.INTERNAL_REFERENCE,)
        if internal_dependencies
        else ()
    )
    required_sources = required_incremental_sources(revision)

    def result(reason: NextUpdateReason | None) -> NextUpdatePolicyEvaluation:
        return NextUpdatePolicyEvaluation(
            policy="incremental_allowed" if reason is None else "full_required",
            reason=reason,
            instrument=instrument,
            mode=mode,
            required_sources=required_sources,
            compatibility_limitations=compatibility_limitations,
        )

    if mode == "off":
        return result(NextUpdateReason.EXPERIMENT_MODE_OFF)
    if (
        not is_supported_equity_symbol(instrument)
        or not instrument.endswith(".T")
        or revision.current_state.instrument != instrument
    ):
        return result(NextUpdateReason.UNSUPPORTED_INCREMENTAL_MARKET)
    if internal_dependencies:
        return result(NextUpdateReason.INVALID_SOURCE_DEPENDENCY)
    try:
        ResearchRevisionDraft.model_validate(
            {field: getattr(revision, field) for field in ResearchRevisionDraft.model_fields}
        )
    except ValueError:
        return result(NextUpdateReason.INVALID_REVISION)
    if (
        revision.cutoff != revision.current_state.cutoff
        or revision.cutoff != revision.evidence_snapshot.bundle.analysis_date
        or instrument != revision.evidence_snapshot.bundle.instrument
    ):
        return result(NextUpdateReason.INVALID_REVISION)
    anchor_reason = _forward_anchor_policy_reason(revision)
    if anchor_reason is not None:
        return result(anchor_reason)
    if revision.execution_strategy is ResearchExecutionStrategy.INCREMENTAL:
        if not _required_source_coverage_complete(revision, required_sources):
            return result(NextUpdateReason.REQUIRED_SOURCE_COVERAGE_INCOMPLETE)
        if not revision.coverage.supports_no_material_change:
            return result(NextUpdateReason.COVERAGE_INCOMPLETE)
        return result(None)
    if any(
        item.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY
        for item in revision.delta.change_signals
    ):
        return result(NextUpdateReason.INCOMPATIBLE_MARKET_SEMANTICS)
    return result(None)


def present_research_chain(
    chain: ResearchChain,
    *,
    mode: Literal["off", "shadow", "experimental"],
) -> ResearchChain:
    """Project persisted Chain state into its current policy-bearing view."""
    revision = chain.current_revision
    if revision is None:
        return chain
    evaluation = evaluate_next_update_policy(
        revision,
        instrument=chain.instrument,
        mode=mode,
    )
    return chain.model_copy(
        update={
            "forward_research_anchor": (
                revision.coverage.anchor_qualification
                or legacy_forward_research_anchor_qualification()
            ),
            "next_update_policy": evaluation.policy,
            "next_update_reason": evaluation.reason,
        }
    )


def derive_shadow_comparison(
    candidate: ResearchRevisionDraft | None,
    authoritative: ResearchRevisionDraft,
) -> Literal["agreement", "disagreement", "inconclusive", "not_applicable"]:
    return derive_shadow_comparison_from_conclusions(
        candidate.change_conclusion if candidate is not None else None,
        authoritative.change_conclusion,
        candidate_present=candidate is not None,
    )


def derive_shadow_comparison_from_conclusions(
    candidate: ResearchChangeConclusion | None,
    authoritative: ResearchChangeConclusion | None,
    *,
    candidate_present: bool,
) -> Literal["agreement", "disagreement", "inconclusive", "not_applicable"]:
    """Compare bounded and authoritative conclusions without duplicating policy."""
    if not candidate_present:
        return "not_applicable"
    if authoritative is ResearchChangeConclusion.INDETERMINATE:
        return "inconclusive"
    if authoritative is ResearchChangeConclusion.NO_MATERIAL_CHANGE:
        return "agreement"
    return "disagreement"
