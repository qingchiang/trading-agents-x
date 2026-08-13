from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from tests.application.test_service import _execution
from tradingagents.application.contracts import (
    AnalysisRequest,
    EvidenceBundle,
    EvidenceItem,
    MarketReferenceLevel,
    ReportLanguage,
    ResearchQuestionSourceDependency,
    ResearchRating,
    ResearchScenarioKind,
    ResearchUpdateAudit,
    ResearchUpdateCandidate,
    ResearchUpdateCoverageAttestation,
    ResearchUpdateSummaryContract,
)
from tradingagents.application.incremental import assess_semantic_update
from tradingagents.application.research import (
    AnchorQualificationReason,
    CapabilitySourceContract,
    ClaimConfidence,
    ClaimStanding,
    CoverageRequirement,
    CoverageStatus,
    CurrentResearchState,
    DecisionConfidence,
    DecisionRole,
    EpistemicKind,
    EvidenceSnapshotItem,
    IncrementalEscalationReason,
    IndeterminateReason,
    MarketResearchCapability,
    MarketResearchCapabilityProfile,
    NextUpdateReason,
    QuestionDispositionAudit,
    QuestionDispositionRecord,
    QuestionStatus,
    ResearchChain,
    ResearchChangeConclusion,
    ResearchChangeKind,
    ResearchChangeSignal,
    ResearchClaim,
    ResearchDomainCoverage,
    ResearchExecutionStrategy,
    ResearchObjectCoverage,
    ResearchOpinion,
    ResearchQuestion,
    ResearchRevision,
    ResearchRevisionRole,
    ResearchScenarioState,
    RevisionExport,
    ScenarioLikelihood,
    SemanticChangeRelationship,
    SourceCoverageLimitation,
    SourceObservationInterval,
    SourceRecordSnapshotItem,
    SourceRecordStatus,
    SourceRecordVersion,
    TransitionContinuityRule,
    assemble_full_revision,
    assemble_full_update,
    assess_deterministic_update,
    bind_information_frontier,
    derive_forward_research_anchor,
    derive_shadow_comparison,
    evaluate_next_update_policy,
    render_revision_export_markdown,
    required_incremental_sources,
    validate_experimental_nmc_candidate,
)

REF = "ev_0123456789ab"
CUTOFF = date(2026, 7, 24)


def _claim(**updates: object) -> ResearchClaim:
    values = {
        "id": "claim_0123456789abcdef0123456789abcdef",
        "statement": "Margin recovery supports the thesis.",
        "epistemic_kind": EpistemicKind.INFERENCE,
        "decision_role": DecisionRole.THESIS,
        "standing": ClaimStanding.ACTIVE,
        "confidence": ClaimConfidence.MEDIUM,
        "evidence_refs": (REF,),
        "falsifier": "Reported margins remain below the prior-year level.",
    }
    values.update(updates)
    return ResearchClaim.model_validate(values)


def _state(*, scenarios: tuple[ResearchScenarioState, ...] | None = None):
    claim = _claim()
    scenario_values = scenarios or tuple(
        ResearchScenarioState(
            kind=kind,
            likelihood=ScenarioLikelihood.INDETERMINATE,
            cutoff=CUTOFF,
            horizon="12 months",
            outcome=f"{kind.value} outcome",
            assumption_claim_ids=(claim.id,),
            evidence_refs=(REF,),
        )
        for kind in ResearchScenarioKind
    )
    return CurrentResearchState(
        language="en",
        instrument="6501.T",
        cutoff=CUTOFF,
        opinion=ResearchOpinion(
            rating=ResearchRating.HOLD,
            confidence=DecisionConfidence.MEDIUM,
            thesis="Evidence supports a conditional thesis.",
            primary_claim_ids=(claim.id,),
            evidence_refs=(REF,),
        ),
        claims=(claim,),
        scenarios=scenario_values,
        evidence_refs=(REF,),
    )


def test_current_research_state_allows_tied_indeterminate_scenario_likelihoods():
    state = _state()

    assert {scenario.likelihood for scenario in state.scenarios} == {
        ScenarioLikelihood.INDETERMINATE
    }
    assert state.opinion.confidence is DecisionConfidence.MEDIUM
    assert state.claims[0].confidence is ClaimConfidence.MEDIUM


def test_capability_profile_supports_complementary_sources_and_alternatives():
    profile = MarketResearchCapabilityProfile(
        id="fixture",
        instrument_suffixes=(".T",),
        bounded_execution_supported=True,
        minimum_anchor_capabilities=(MarketResearchCapability.OFFICIAL_FILING,),
        source_contracts=(
            CapabilitySourceContract(
                capability=MarketResearchCapability.OFFICIAL_FILING,
                transition_continuity=TransitionContinuityRule.EVENT_STREAM,
                acceptable_source_sets=(("source-a", "source-b"), ("source-c",)),
            ),
        ),
    )

    assert profile.source_contracts[0].acceptable_source_sets == (
        ("source-a", "source-b"),
        ("source-c",),
    )
    assert (
        profile.source_contracts[0].transition_continuity is TransitionContinuityRule.EVENT_STREAM
    )


def test_japanese_full_revision_derives_provider_independent_anchor_coverage():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    frontier = datetime(2026, 7, 24, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
    anchored = bind_information_frontier(baseline, frontier)

    qualification = derive_forward_research_anchor(anchored)

    assert qualification.is_forward_research_anchor is True
    assert qualification.reasons == ()
    assert {item.capability for item in qualification.capabilities if item.required} == {
        MarketResearchCapability.OFFICIAL_FILING,
        MarketResearchCapability.TIMELY_DISCLOSURE,
        MarketResearchCapability.MARKET_OBSERVATION,
    }
    assert all(item.satisfied for item in qualification.capabilities if item.required)


def test_pre_frontier_archive_truncation_is_retained_without_blocking_anchor():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    tdnet = next(
        item for item in baseline.evidence_snapshot.source_watermarks if item.source == "TDnet"
    )
    limited_tdnet = tdnet.model_copy(
        update={
            "status": CoverageStatus.LIMITED,
            "limitations": ("Older archive history is unavailable.",),
            "structured_limitations": (
                SourceCoverageLimitation(
                    kind="archive_truncation",
                    temporal_scope="point_in_time",
                    requested_interval=SourceObservationInterval(
                        start=date(2026, 6, 1), end=CUTOFF
                    ),
                    observed_intervals=(
                        SourceObservationInterval(start=date(2026, 7, 1), end=CUTOFF),
                    ),
                    presentation_text="Older archive history is unavailable.",
                ),
            ),
        }
    )
    limited = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        limited_tdnet if item.source == "TDnet" else item
                        for item in baseline.evidence_snapshot.source_watermarks
                    )
                }
            )
        }
    )

    qualification = derive_forward_research_anchor(limited)

    assert qualification.is_forward_research_anchor is True
    assert limited_tdnet.limitations == ("Older archive history is unavailable.",)


def test_correction_assessed_by_full_analysis_does_not_permanently_block_anchor():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    evidence_ref = baseline.evidence_snapshot.bundle.items[0].ref
    corrected = SourceRecordVersion(
        source="EDINET",
        record_id="filing-1",
        version_id="filing-1:v2",
        status=SourceRecordStatus.CORRECTED,
        published_at="2026-07-24 10:00",
        available_at=datetime(2026, 7, 24, 10, tzinfo=ZoneInfo("Asia/Tokyo")),
        title="Corrected filing",
        evidence_ref=evidence_ref,
    )
    assessed = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_records": (*baseline.evidence_snapshot.source_records, corrected),
                    "source_record_lineage": (
                        *baseline.evidence_snapshot.source_record_lineage,
                        SourceRecordSnapshotItem(
                            version_id=corrected.version_id,
                            lineage="new",
                            observed_in_execution=True,
                        ),
                    ),
                }
            )
        }
    )

    assert derive_forward_research_anchor(assessed).is_forward_research_anchor is True


def test_source_record_observed_after_frontier_fails_anchor_point_in_time_validity():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    future_record = baseline.evidence_snapshot.source_records[0].model_copy(
        update={"available_at": baseline.information_frontier + timedelta(minutes=1)}
    )
    future = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={"source_records": (future_record,)}
            )
        }
    )

    assert AnchorQualificationReason.POINT_IN_TIME_INVALID in (
        derive_forward_research_anchor(future).reasons
    )


@pytest.mark.parametrize(
    ("cutoff", "published_at"),
    [
        (date(2026, 7, 24), "2026-07-23"),
        (date(2026, 7, 25), "2026-07-23"),
    ],
)
def test_market_observation_requires_latest_permitted_completed_session(
    cutoff,
    published_at,
):
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    stale_market = baseline.evidence_snapshot.source_records[0].model_copy(
        update={"published_at": published_at}
    )
    stale = baseline.model_copy(
        update={
            "cutoff": cutoff,
            "current_state": baseline.current_state.model_copy(update={"cutoff": cutoff}),
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "bundle": baseline.evidence_snapshot.bundle.model_copy(
                        update={"analysis_date": cutoff}
                    ),
                    "source_records": (stale_market,),
                    "source_watermarks": tuple(
                        item.model_copy(update={"scanned_end": cutoff})
                        for item in baseline.evidence_snapshot.source_watermarks
                    ),
                }
            ),
        }
    )

    qualification = derive_forward_research_anchor(stale)

    market = next(
        item
        for item in qualification.capabilities
        if item.capability is MarketResearchCapability.MARKET_OBSERVATION
    )
    assert market.satisfied is False


def test_invalid_market_observation_date_fails_anchor_without_raising():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    invalid = baseline.evidence_snapshot.source_records[0].model_copy(
        update={"published_at": "unknown"}
    )

    qualification = derive_forward_research_anchor(
        baseline.model_copy(
            update={
                "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                    update={"source_records": (invalid,)}
                )
            }
        )
    )

    assert qualification.is_forward_research_anchor is False
    assert AnchorQualificationReason.REQUIRED_CAPABILITY_MISSING in qualification.reasons


def test_unavailable_open_question_fails_anchor_even_without_declared_sources():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    question = ResearchQuestion(
        id="question_0123456789abcdef0123456789abcdef",
        question="Was this uncertainty checked?",
    )
    unavailable = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"questions": (question,)}),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.UNAVAILABLE,
                        ),
                    )
                }
            ),
        }
    )

    assert AnchorQualificationReason.REQUIRED_DOMAIN_MISSING in (
        derive_forward_research_anchor(unavailable).reasons
    )


@pytest.mark.parametrize(
    ("source", "capability"),
    [
        ("Google News", MarketResearchCapability.MEDIA),
        ("Social sentiment", MarketResearchCapability.SOCIAL_SENTIMENT),
        ("Macro observations", MarketResearchCapability.MACRO),
    ],
)
def test_research_object_can_promote_advisory_capability(source, capability):
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    claim = baseline.current_state.claims[0].model_copy(update={"required_sources": (source,)})
    promoted = baseline.model_copy(
        update={"current_state": baseline.current_state.model_copy(update={"claims": (claim,)})}
    )

    qualification = derive_forward_research_anchor(promoted)

    attestation = next(item for item in qualification.capabilities if item.capability is capability)
    assert attestation.required is True
    assert attestation.satisfied is False


def test_promoted_advisory_capability_requires_completed_audit():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    claim = baseline.current_state.claims[0].model_copy(
        update={"required_sources": ("Google News",)}
    )
    news_domain = ResearchDomainCoverage(
        domain="news",
        requirement=CoverageRequirement.ADVISORY,
        status=CoverageStatus.LIMITED,
        limitations=("news audit incomplete",),
    )
    promoted = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"claims": (claim,)}),
            "coverage": baseline.coverage.model_copy(
                update={"domains": (*baseline.coverage.domains, news_domain)}
            ),
        }
    )

    assert AnchorQualificationReason.FULL_AUDIT_INCOMPLETE in (
        derive_forward_research_anchor(promoted).reasons
    )


def test_limited_decision_envelope_claim_remains_usable_anchor_state():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    claim = baseline.current_state.claims[0].model_copy(
        update={"evidence_relationship": "decision_envelope"}
    )
    limited = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"claims": (claim,)}),
            "coverage": baseline.coverage.model_copy(
                update={
                    "claims": (
                        baseline.coverage.claims[0].model_copy(
                            update={"status": CoverageStatus.LIMITED}
                        ),
                    )
                }
            ),
        }
    )

    assert derive_forward_research_anchor(limited).is_forward_research_anchor is True


def test_open_question_is_anchor_covered_when_required_domain_was_checked():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    question = ResearchQuestion(
        id="question_0123456789abcdef0123456789abcdef",
        question="Will the filing resolve the uncertainty?",
        status=QuestionStatus.OPEN,
        required_sources=("EDINET",),
    )
    checked = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"questions": (question,)}),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.LIMITED,
                            limitations=("Question remains open after checking EDINET.",),
                        ),
                    )
                }
            ),
        }
    )
    checked = bind_information_frontier(
        checked, datetime(2026, 7, 24, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
    )

    assert derive_forward_research_anchor(checked).is_forward_research_anchor is True


def test_question_missing_required_domain_fails_anchor_coverage():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    question = ResearchQuestion(
        id="question_0123456789abcdef0123456789abcdef",
        question="Will an unavailable source resolve the uncertainty?",
        status=QuestionStatus.OPEN,
        required_sources=("Unconfigured source",),
    )
    missing = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"questions": (question,)}),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.UNAVAILABLE,
                            limitations=("Required Domain was not checked.",),
                        ),
                    )
                }
            ),
        }
    )
    missing = bind_information_frontier(
        missing, datetime(2026, 7, 24, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
    )

    qualification = derive_forward_research_anchor(missing)

    assert qualification.is_forward_research_anchor is False
    assert AnchorQualificationReason.REQUIRED_DOMAIN_MISSING in qualification.reasons


def test_qualifying_indeterminate_full_revision_remains_a_forward_anchor():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    indeterminate = baseline.model_copy(
        update={
            "role": ResearchRevisionRole.UPDATE,
            "change_conclusion": ResearchChangeConclusion.INDETERMINATE,
            "indeterminate_reason": IndeterminateReason.COVERAGE_INCOMPLETE,
        }
    )
    indeterminate = bind_information_frontier(
        indeterminate, datetime(2026, 7, 24, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
    )

    assert indeterminate.coverage.anchor_qualification.is_forward_research_anchor is True
    assert (
        evaluate_next_update_policy(indeterminate, instrument="6501.T", mode="experimental").policy
        == "incremental_allowed"
    )


def test_incremental_sources_include_profile_minimum_without_explicit_domains():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    baseline = baseline.model_copy(
        update={
            "coverage": baseline.coverage.model_copy(update={"domains": ()}),
            "current_state": baseline.current_state.model_copy(
                update={
                    "claims": tuple(
                        claim.model_copy(update={"required_sources": ()})
                        for claim in baseline.current_state.claims
                    ),
                    "questions": tuple(
                        question.model_copy(update={"required_sources": ()})
                        for question in baseline.current_state.questions
                    ),
                }
            ),
        }
    )

    assert set(required_incremental_sources(baseline)) == {
        "EDINET",
        "TDnet",
        "J-Quants adjusted OHLCV",
    }


def test_current_research_state_requires_one_shared_scenario_horizon():
    claim = _claim()
    scenarios = tuple(
        ResearchScenarioState(
            kind=kind,
            likelihood=ScenarioLikelihood.LOW,
            cutoff=CUTOFF,
            horizon="24 months" if kind is ResearchScenarioKind.BULL else "12 months",
            outcome=f"{kind.value} outcome",
            assumption_claim_ids=(claim.id,),
            evidence_refs=(REF,),
        )
        for kind in ResearchScenarioKind
    )

    with pytest.raises(ValidationError, match="share horizon"):
        _state(scenarios=scenarios)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"epistemic_kind": EpistemicKind.OBSERVATION, "falsifier": None},
            "observed_at",
        ),
        ({"falsifier": None}, "falsifier"),
    ],
)
def test_research_claim_requires_observation_dates_or_falsifiers(updates, message):
    with pytest.raises(ValidationError, match=message):
        _claim(**updates)


def test_full_state_assembly_assigns_ids_and_preserves_selected_language():
    execution = _execution("6501.T")
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date=CUTOFF,
        analysts=("market",),
        output_language=ReportLanguage.JAPANESE,
    )

    draft = assemble_full_revision(request, execution)

    state = draft.current_state
    assert state.language == "ja"
    assert state.instrument == "6501.T"
    assert state.cutoff == CUTOFF
    assert state.opinion.primary_claim_ids[0].startswith("claim_")
    assert all(claim.id.startswith("claim_") for claim in state.claims)
    assert all(question.id.startswith("question_") for question in state.questions)
    assert draft.execution_strategy.value == "full"
    assert draft.role is ResearchRevisionRole.INITIAL
    assert draft.change_conclusion is None
    assert draft.coverage.domains[0].domain == "market"
    assert draft.evidence_snapshot.bundle.digest == execution.evidence.digest
    assert {item.lineage for item in draft.evidence_snapshot.lineage} == {"new"}
    assert draft.update_summary.language == "ja"
    missing_sources = {item.source for item in draft.coverage.domains if item.source}
    assert missing_sources == {"EDINET", "TDnet", "J-Quants adjusted OHLCV"}
    assert draft.coverage.supports_no_material_change is False


def test_revision_role_and_change_conclusion_are_independent_contracts():
    initial = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=CUTOFF,
            analysts=("market",),
        ),
        _execution("6501.T"),
    )

    with pytest.raises(ValidationError, match="initial Revision has no Change Conclusion"):
        initial.model_copy(
            update={"change_conclusion": ResearchChangeConclusion.MATERIAL_CHANGE}
        ).__class__.model_validate(
            initial.model_copy(
                update={"change_conclusion": ResearchChangeConclusion.MATERIAL_CHANGE}
            ).model_dump(mode="python")
        )


def test_next_update_policy_is_derived_from_complete_baseline_eligibility():
    initial = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _execution("6501.T"),
    )
    initial_policy = evaluate_next_update_policy(initial, instrument="6501.T", mode="experimental")
    assert (initial_policy.policy, initial_policy.reason) == (
        "full_required",
        "legacy_anchor_coverage_unproven",
    )

    complete = initial.model_copy(
        update={
            "coverage": initial.coverage.model_copy(
                update={
                    "claims": tuple(
                        item.model_copy(update={"status": CoverageStatus.COMPLETE})
                        for item in initial.coverage.claims
                    ),
                    "questions": tuple(
                        item.model_copy(update={"status": CoverageStatus.COMPLETE})
                        for item in initial.coverage.questions
                    ),
                    "domains": tuple(
                        item.model_copy(update={"status": CoverageStatus.COMPLETE})
                        for item in initial.coverage.domains
                    ),
                    "supports_no_material_change": True,
                }
            )
        }
    )
    complete_policy = evaluate_next_update_policy(
        complete, instrument="6501.T", mode="experimental"
    )
    assert (complete_policy.policy, complete_policy.reason) == (
        "full_required",
        "legacy_anchor_coverage_unproven",
    )

    indeterminate = complete.model_copy(
        update={
            "role": ResearchRevisionRole.UPDATE,
            "change_conclusion": ResearchChangeConclusion.INDETERMINATE,
            "indeterminate_reason": IndeterminateReason.COVERAGE_INCOMPLETE,
        }
    )
    indeterminate_policy = evaluate_next_update_policy(
        indeterminate, instrument="6501.T", mode="experimental"
    )
    assert (indeterminate_policy.policy, indeterminate_policy.reason) == (
        "full_required",
        "legacy_anchor_coverage_unproven",
    )
    assert derive_shadow_comparison(complete, indeterminate) == "inconclusive"

    with pytest.raises(ValidationError, match="update Revision requires a Change Conclusion"):
        initial.__class__.model_validate(
            initial.model_copy(update={"role": ResearchRevisionRole.UPDATE}).model_dump(
                mode="python"
            )
        )


@pytest.mark.parametrize(
    ("path", "unknown_ref"),
    [
        ("coverage", "ev_111111111111"),
        ("delta", "ev_222222222222"),
        ("update_summary", "ev_333333333333"),
    ],
)
def test_revision_draft_rejects_every_unresolved_reachable_evidence_reference(
    path: str,
    unknown_ref: str,
):
    draft = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=CUTOFF,
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    if path == "coverage":
        value = draft.coverage.model_copy(
            update={
                "domains": (
                    draft.coverage.domains[0].model_copy(update={"evidence_refs": (unknown_ref,)}),
                    *draft.coverage.domains[1:],
                )
            }
        )
    elif path == "delta":
        value = draft.delta.model_copy(update={"new_evidence_refs": (unknown_ref,)})
    else:
        value = draft.update_summary.model_copy(update={"new_evidence_refs": (unknown_ref,)})

    with pytest.raises(ValidationError, match="Evidence closure"):
        draft.__class__.model_validate(
            draft.model_copy(update={path: value}).model_dump(mode="python")
        )


@pytest.mark.parametrize(
    "path",
    ["audit.coverage", "audit.candidate.coverage", "audit.candidate.summary"],
)
def test_revision_draft_rejects_unresolved_typed_audit_evidence(path: str):
    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _execution("6501.T"),
    )
    unknown_ref = "ev_444444444444"
    coverage = ResearchUpdateCoverageAttestation.model_validate(
        draft.coverage.model_dump(mode="python", exclude={"anchor_qualification"})
    )
    summary = ResearchUpdateSummaryContract.model_validate(
        draft.update_summary.model_dump(mode="python")
    )
    audit_coverage = coverage
    candidate_coverage = coverage
    candidate_summary = summary
    if path == "audit.coverage":
        audit_coverage = coverage.model_copy(
            update={
                "domains": (
                    coverage.domains[0].model_copy(update={"evidence_refs": (unknown_ref,)}),
                    *coverage.domains[1:],
                )
            }
        )
    elif path == "audit.candidate.coverage":
        candidate_coverage = coverage.model_copy(
            update={
                "domains": (
                    coverage.domains[0].model_copy(update={"evidence_refs": (unknown_ref,)}),
                    *coverage.domains[1:],
                )
            }
        )
    else:
        candidate_summary = summary.model_copy(update={"new_evidence_refs": (unknown_ref,)})
    audit = ResearchUpdateAudit(
        candidate=ResearchUpdateCandidate(
            change_conclusion="no_material_change",
            coverage=candidate_coverage,
            update_summary=candidate_summary,
            evidence_snapshot=draft.evidence_snapshot.model_dump(mode="python"),
        ),
        coverage=audit_coverage,
        comparison="inconclusive",
    )

    with pytest.raises(ValidationError, match="Evidence closure"):
        draft.__class__.model_validate(
            draft.model_copy(update={"research_update_audit": audit}).model_dump(mode="python")
        )


def test_revision_draft_rejects_source_record_evidence_outside_snapshot():
    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[_source_record("edinet:S100ROOT")],
            watermarks=[_watermark("EDINET")],
        ),
    )
    broken_snapshot = draft.evidence_snapshot.model_copy(
        update={
            "source_records": (
                draft.evidence_snapshot.source_records[0].model_copy(
                    update={"evidence_ref": "ev_555555555555"}
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="Evidence closure"):
        draft.__class__.model_validate(
            draft.model_copy(update={"evidence_snapshot": broken_snapshot}).model_dump(
                mode="python"
            )
        )


def test_revision_draft_rejects_change_signal_version_outside_snapshot():
    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[_source_record("edinet:S100ROOT")],
            watermarks=[_watermark("EDINET")],
        ),
    )
    broken_delta = draft.delta.model_copy(
        update={
            "change_signals": (
                ResearchChangeSignal(
                    kind=ResearchChangeKind.FUNDAMENTAL_CORRECTION,
                    domain="fundamentals",
                    record_id="S100ROOT",
                    previous_version_id="edinet:missing",
                    current_version_id="edinet:S100ROOT",
                    requires_full_analysis=True,
                    detail="Correction lineage must remain closed.",
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="Source Record closure"):
        draft.__class__.model_validate(
            draft.model_copy(update={"delta": broken_delta}).model_dump(mode="python")
        )


def test_full_state_assembly_rejects_unresolved_non_edinet_direct_predecessor():
    unresolved_tdnet = {
        **_source_record("tdnet:new", replaces="tdnet:missing"),
        "source": "TDnet",
        "record_id": "tdnet-record",
    }

    with pytest.raises(ValidationError, match="Source Record closure"):
        assemble_full_revision(
            AnalysisRequest(
                ticker="6501.T",
                analysis_date=CUTOFF,
                analysts=("market",),
            ),
            _with_disclosure_metadata(
                _execution("6501.T"),
                records=[unresolved_tdnet],
                watermarks=[_watermark("TDnet")],
            ),
        )


def test_full_state_assembly_rejects_missing_explicit_claim_evidence():
    execution = _execution("6501.T")
    report = execution.reports["market"].model_copy(
        update={
            "key_claims": tuple(
                claim.model_copy(update={"evidence_refs": ()})
                for claim in execution.reports["market"].key_claims
            )
        }
    )
    execution = execution.__class__(
        state=execution.state,
        evidence=execution.evidence,
        reports={"market": report},
        decision=execution.decision,
    )

    with pytest.raises(ValueError, match="explicit Evidence"):
        assemble_full_revision(
            AnalysisRequest(
                ticker="6501.T",
                analysis_date=CUTOFF,
                analysts=("market",),
            ),
            execution,
        )


def test_full_update_preserves_only_unambiguous_longitudinal_identities():
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date=date(2026, 7, 25),
        analysts=("market",),
    )
    baseline = assemble_full_revision(
        request.model_copy(update={"analysis_date": CUTOFF}),
        _execution("6501.T"),
    )
    candidate = assemble_full_revision(request, _execution("6501.T"))

    updated = assemble_full_update("revision-1", baseline, candidate)
    assert updated.current_state.claims[0].id == baseline.current_state.claims[0].id
    assert updated.delta.claims[0].change.value == "reaffirmed"
    assert {item.lineage for item in updated.evidence_snapshot.lineage} == {"new"}
    assert updated.execution_strategy.value == "full"


def _with_disclosure_metadata(execution, *, records, watermarks):
    evidence = execution.evidence.items[0].model_copy(
        update={
            "provenance": {
                "source_records": records,
                "source_watermarks": watermarks,
            }
        }
    )
    return execution.__class__(
        state=execution.state,
        evidence=EvidenceBundle(
            instrument=execution.evidence.instrument,
            analysis_date=execution.evidence.analysis_date,
            items=(evidence,),
        ),
        reports=execution.reports,
        decision=execution.decision,
    )


def _source_record(version_id: str, *, status="published", replaces=None):
    return {
        "source": "EDINET",
        "record_id": "S100ROOT",
        "version_id": version_id,
        "status": status,
        "published_at": "2026-07-23 15:00",
        "available_at": "2026-07-23T15:00:00+09:00",
        "title": "訂正有価証券報告書" if status == "corrected" else "有価証券報告書",
        "replaces_version_id": replaces,
    }


def _watermark(
    source: str,
    *,
    status="complete",
    limitations=(),
    temporal_scope="point_in_time",
    returned_records=1,
    reported_records=1,
):
    return {
        "source": source,
        "scanned_start": "2026-07-01",
        "scanned_end": "2026-07-24",
        "status": status,
        "temporal_scope": temporal_scope,
        "limitations": limitations,
        "returned_records": returned_records,
        "reported_records": reported_records,
    }


def _incremental_baseline_and_evidence(
    *,
    candidate_records=None,
    candidate_watermarks=None,
):
    market = {
        **_source_record("market:v1"),
        "source": "J-Quants adjusted OHLCV",
        "record_id": "jquants-market:6501",
        "record_kind": "market",
        "adjustment": "split_adjusted",
        "observation_value": 95.0,
        "unit": "JPY",
        "published_at": "2026-07-24",
        "available_at": "2026-07-24T17:00:00+09:00",
    }
    watermarks = [
        _watermark("EDINET", returned_records=0, reported_records=0),
        _watermark("TDnet", returned_records=0, reported_records=0),
        _watermark("J-Quants adjusted OHLCV"),
        _watermark("Google News"),
    ]
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[market],
            watermarks=watermarks,
        ),
    )
    baseline = baseline.model_copy(
        update={
            "coverage": baseline.coverage.model_copy(
                update={
                    "claims": tuple(
                        item.model_copy(
                            update={"status": CoverageStatus.COMPLETE, "limitations": ()}
                        )
                        for item in baseline.coverage.claims
                    ),
                    "limitations": (),
                    "supports_no_material_change": True,
                }
            )
        }
    )
    baseline = bind_information_frontier(
        baseline,
        datetime.combine(
            date(2026, 7, 24),
            datetime.max.time(),
            tzinfo=ZoneInfo("Asia/Tokyo"),
        ),
    )
    selected_watermarks = (
        candidate_watermarks
        if candidate_watermarks is not None
        else [{**item, "scanned_end": "2026-07-25"} for item in watermarks]
    )
    selected_watermarks = [
        {
            **item,
            **(
                {}
                if "information_frontier" in item
                else {"information_frontier": "2026-07-25T23:59:59.999999+09:00"}
            ),
        }
        for item in selected_watermarks
    ]
    item = EvidenceItem.create(
        source="bounded fixture",
        evidence_type="bounded update",
        requested_date=date(2026, 7, 25),
        effective_date=date(2026, 7, 25),
        content="Bounded source observations.",
        provenance={
            "source_records": candidate_records if candidate_records is not None else [market],
            "source_watermarks": selected_watermarks,
        },
    )
    evidence = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 7, 25),
        information_frontier=datetime(
            2026, 7, 25, 23, 59, 59, 999999, tzinfo=ZoneInfo("Asia/Tokyo")
        ),
        items=(item,),
    )
    return baseline, evidence, market, watermarks


def _tdnet_archive_transition_watermark(*, observed_start: str) -> dict[str, object]:
    return {
        **_watermark(
            "TDnet",
            status="limited",
            limitations=("Requested interval was truncated by the TDnet rolling archive.",),
            returned_records=0,
            reported_records=0,
        ),
        "scanned_start": observed_start,
        "scanned_end": "2026-07-25",
        "requested_interval": {"start": "2026-07-01", "end": "2026-07-25"},
        "limitation_kind": "archive_truncation",
    }


@pytest.mark.parametrize(
    ("capability", "source", "returned_records", "expected_complete", "expected_gap"),
    [
        (MarketResearchCapability.OFFICIAL_FILING, "EDINET", 0, False, date(2026, 7, 25)),
        (MarketResearchCapability.FUNDAMENTALS, "J-Quants fundamentals", 0, True, None),
        (
            MarketResearchCapability.MARKET_OBSERVATION,
            "J-Quants adjusted OHLCV",
            1,
            True,
            None,
        ),
    ],
)
def test_transition_coverage_applies_capability_specific_continuity(
    capability,
    source,
    returned_records,
    expected_complete,
    expected_gap,
):
    baseline, _evidence, market, _watermarks = _incremental_baseline_and_evidence()
    qualification = baseline.coverage.anchor_qualification
    assert qualification is not None
    baseline = baseline.model_copy(
        update={
            "coverage": baseline.coverage.model_copy(
                update={
                    "anchor_qualification": qualification.model_copy(
                        update={
                            "capabilities": tuple(
                                item.model_copy(update={"required": item.capability is capability})
                                for item in qualification.capabilities
                            )
                        }
                    )
                }
            )
        }
    )
    update_frontier = datetime(2026, 7, 27, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
    candidate_records = []
    if capability is MarketResearchCapability.MARKET_OBSERVATION:
        candidate_records.append(
            {
                **market,
                "version_id": "market:v2",
                "published_at": "2026-07-27",
                "available_at": "2026-07-27T17:00:00+09:00",
            }
        )
    item = EvidenceItem.create(
        source="bounded fixture",
        evidence_type="bounded update",
        requested_date=date(2026, 7, 27),
        effective_date=date(2026, 7, 27),
        content="Capability-specific continuity fixture.",
        provenance={
            "source_records": candidate_records,
            "source_watermarks": [
                {
                    "source": source,
                    "scanned_start": "2026-07-27",
                    "scanned_end": "2026-07-27",
                    "status": "complete",
                    "returned_records": returned_records,
                    "reported_records": returned_records,
                    "requested_interval": {"start": "2026-07-27", "end": "2026-07-27"},
                    "information_frontier": update_frontier.isoformat(),
                }
            ],
        },
    )
    evidence = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 7, 27),
        information_frontier=update_frontier,
        items=(item,),
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-27", analysts=("market",)),
        evidence,
        information_frontier=update_frontier,
    )

    transition = next(
        item for item in result.transition_coverage.capabilities if item.capability is capability
    )
    assert transition.complete is expected_complete, result.model_dump(mode="json")
    assert ([item.start for item in transition.gaps] or [None])[0] == expected_gap


def test_transition_coverage_ignores_tdnet_archive_gap_wholly_before_anchor():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [
        ({**item, "scanned_end": "2026-07-25"}) for item in watermarks if item["source"] != "TDnet"
    ]
    candidate_watermarks.append(_tdnet_archive_transition_watermark(observed_start="2026-07-24"))
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is None
    assert result.candidate is not None
    assert result.transition_coverage is not None
    assert result.transition_coverage.complete is True
    tdnet = next(
        item
        for item in result.transition_coverage.capabilities
        if item.capability is MarketResearchCapability.TIMELY_DISCLOSURE
    )
    assert tdnet.complete is True
    assert tdnet.limitations[0].scope == "pre_anchor"


def test_transition_coverage_blocks_tdnet_archive_gap_intersecting_transition():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    baseline = bind_information_frontier(
        baseline,
        datetime(2026, 7, 24, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )
    candidate_watermarks = [
        ({**item, "scanned_end": "2026-07-25"}) for item in watermarks if item["source"] != "TDnet"
    ]
    candidate_watermarks.append(_tdnet_archive_transition_watermark(observed_start="2026-07-25"))
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.candidate is None
    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    assert result.transition_coverage is not None
    assert result.transition_coverage.complete is False
    tdnet = next(
        item
        for item in result.transition_coverage.capabilities
        if item.capability is MarketResearchCapability.TIMELY_DISCLOSURE
    )
    assert tdnet.complete is False
    assert tdnet.limitations[0].scope == "transition"


def test_transition_preserves_an_earlier_source_frontier_and_fails_closed():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [
        {
            **item,
            "scanned_end": "2026-07-25",
            **(
                {"information_frontier": "2026-07-25T17:00:00+09:00"}
                if item["source"] == "TDnet"
                else {}
            ),
        }
        for item in watermarks
    ]
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    tdnet = next(
        item for item in result.evidence_snapshot.source_watermarks if item.source == "TDnet"
    )
    assert tdnet.information_frontier == datetime(2026, 7, 25, 17, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def test_transition_requires_an_explicit_common_information_frontier():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    evidence = evidence.model_copy(update={"information_frontier": None})

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.candidate is None
    assert result.transition_coverage is None
    assert result.escalation_reason is IncrementalEscalationReason.SCHEMA_INVALID


def test_transition_does_not_merge_a_missing_source_frontier_into_attested_coverage():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    tdnet = next(
        item
        for item in evidence.items[0].provenance["source_watermarks"]
        if item["source"] == "TDnet"
    )
    missing_frontier = EvidenceItem.create(
        source="second TDnet collector",
        evidence_type="bounded update",
        requested_date=date(2026, 7, 25),
        effective_date=date(2026, 7, 25),
        content="A duplicate source scan without a frontier attestation.",
        provenance={"source_watermarks": [{**tdnet, "information_frontier": None}]},
    )
    evidence = evidence.model_copy(update={"items": (*evidence.items, missing_frontier)})

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.candidate is None
    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    assert result.transition_coverage is not None
    assert result.transition_coverage.complete is False
    merged_tdnet = next(
        item for item in result.evidence_snapshot.source_watermarks if item.source == "TDnet"
    )
    assert merged_tdnet.information_frontier is None


@pytest.mark.parametrize(
    "tdnet_update",
    [
        {"information_frontier": None},
        {"returned_records": 0, "reported_records": None},
    ],
)
def test_empty_transition_requires_explicit_frontier_and_zero_attestation(tdnet_update):
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [
        {
            **item,
            "scanned_end": "2026-07-25",
            **(tdnet_update if item["source"] == "TDnet" else {}),
        }
        for item in watermarks
    ]
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    assert result.transition_coverage.complete is False


def test_non_archive_limitation_wholly_before_anchor_does_not_block_transition():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [
        ({**item, "scanned_end": "2026-07-25"}) for item in watermarks if item["source"] != "TDnet"
    ]
    candidate_watermarks.append(
        {
            **_tdnet_archive_transition_watermark(observed_start="2026-07-24"),
            "limitation_kind": "partial",
        }
    )
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is None
    tdnet = next(
        item
        for item in result.transition_coverage.capabilities
        if item.capability is MarketResearchCapability.TIMELY_DISCLOSURE
    )
    assert tdnet.limitations[0].scope == "pre_anchor"


def test_complete_typed_limitation_window_wholly_before_anchor_does_not_block():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [
        ({**item, "scanned_end": "2026-07-25"}) for item in watermarks if item["source"] != "TDnet"
    ]
    candidate_watermarks.extend(
        (
            {
                **_watermark(
                    "TDnet",
                    status="limited",
                    limitations=("Historical source limitation.",),
                    returned_records=0,
                    reported_records=None,
                ),
                "scanned_start": "2026-07-01",
                "scanned_end": "2026-07-23",
                "requested_interval": {"start": "2026-07-01", "end": "2026-07-23"},
                "limitation_kind": "partial",
                "information_frontier": None,
            },
            {
                **_watermark("TDnet", returned_records=0, reported_records=0),
                "scanned_start": "2026-07-25",
                "scanned_end": "2026-07-25",
                "requested_interval": {"start": "2026-07-25", "end": "2026-07-25"},
            },
        )
    )
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is None
    tdnet = next(
        item
        for item in result.transition_coverage.capabilities
        if item.capability is MarketResearchCapability.TIMELY_DISCLOSURE
    )
    assert tdnet.complete is True
    assert tdnet.limitations[0].scope == "pre_anchor"


def _tdnet_record(*, version_id: str, available_at: str) -> dict[str, object]:
    return {
        **_source_record(version_id),
        "source": "TDnet",
        "record_id": "tdnet-same-day",
        "published_at": "2026-07-24 20:00",
        "available_at": available_at,
        "title": "Late same-day timely disclosure",
    }


def test_transition_treats_same_day_event_after_anchor_frontier_as_new():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    baseline = bind_information_frontier(
        baseline,
        datetime(2026, 7, 24, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )
    candidate_watermarks = [{**item, "scanned_end": "2026-07-25"} for item in watermarks]
    candidate_watermarks[1] = {
        **candidate_watermarks[1],
        "returned_records": 1,
        "reported_records": 1,
    }
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[
            market,
            _tdnet_record(
                version_id="tdnet:late-same-day",
                available_at="2026-07-24T20:00:00+09:00",
            ),
        ],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is IncrementalEscalationReason.SOURCE_VERSION_CHANGE
    late = next(
        item
        for item in result.evidence_snapshot.source_record_lineage
        if item.version_id == "tdnet:late-same-day"
    )
    assert late.lineage == "new"
    assert late.observed_in_execution is True


def test_observation_after_update_frontier_cannot_prove_transition():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [{**item, "scanned_end": "2026-07-25"} for item in watermarks]
    candidate_watermarks[1] = {
        **candidate_watermarks[1],
        "returned_records": 1,
        "reported_records": 1,
    }
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[
            market,
            _tdnet_record(
                version_id="tdnet:after-frontier",
                available_at="2026-07-25T20:00:00+09:00",
            ),
        ],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    assert all(
        item.version_id != "tdnet:after-frontier"
        for item in result.evidence_snapshot.source_records
    )


@pytest.mark.parametrize("temporal_scope", ["live_only", "unknown"])
def test_required_transition_rejects_unprovable_temporal_scope(temporal_scope):
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    candidate_watermarks = [
        {
            **item,
            "scanned_end": "2026-07-25",
            **({"temporal_scope": temporal_scope} if item["source"] == "TDnet" else {}),
        }
        for item in watermarks
    ]
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
        information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE


@pytest.mark.parametrize(
    ("mode", "ticker", "revision_update", "expected_reason"),
    [
        ("off", "6501.T", {}, "experiment_mode_off"),
        ("experimental", "NVDA", {}, "unsupported_incremental_market"),
    ],
)
def test_next_update_policy_has_stable_mode_market_and_head_reasons(
    mode,
    ticker,
    revision_update,
    expected_reason,
):
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    if revision_update:
        baseline = baseline.model_copy(
            update={
                "role": ResearchRevisionRole.UPDATE,
                "execution_strategy": ResearchExecutionStrategy.FULL,
                "indeterminate_reason": IndeterminateReason.COVERAGE_INCOMPLETE,
                **revision_update,
            }
        )

    result = evaluate_next_update_policy(baseline, instrument=ticker, mode=mode)

    assert result.policy == "full_required"
    assert result.reason == expected_reason


def test_next_update_policy_requires_source_qualified_japanese_coverage():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    baseline = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        item.model_copy(update={"returned_records": 0, "reported_records": 0})
                        if item.source in {"EDINET", "TDnet"}
                        else item
                        for item in baseline.evidence_snapshot.source_watermarks
                    )
                }
            )
        }
    )
    eligible = evaluate_next_update_policy(
        baseline,
        instrument="6501.T",
        mode="experimental",
    )
    assert eligible.policy == "incremental_allowed"
    assert eligible.reason is None

    missing_tdnet = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        item
                        for item in baseline.evidence_snapshot.source_watermarks
                        if item.source != "TDnet"
                    )
                }
            )
        }
    )
    refused = evaluate_next_update_policy(
        missing_tdnet,
        instrument="6501.T",
        mode="shadow",
    )
    assert refused.policy == "full_required"
    assert refused.reason == "anchor_coverage_incomplete"


def test_next_update_policy_rejects_positive_watermark_without_observed_version():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    invalid = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        item.model_copy(update={"returned_records": 1, "reported_records": 1})
                        if item.source == "TDnet"
                        else item
                        for item in baseline.evidence_snapshot.source_watermarks
                    )
                }
            )
        }
    )

    result = evaluate_next_update_policy(
        invalid,
        instrument="6501.T",
        mode="experimental",
    )

    assert result.reason == "anchor_coverage_incomplete"


def test_bounded_update_rejects_positive_results_without_observed_version():
    candidate_watermarks = [
        {
            **_watermark(source, returned_records=returned, reported_records=returned),
            "scanned_end": "2026-07-25",
        }
        for source, returned in (
            ("EDINET", 0),
            ("TDnet", 1),
            ("J-Quants adjusted OHLCV", 0),
            ("Google News", 0),
        )
    ]
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence(
        candidate_records=[],
        candidate_watermarks=candidate_watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE


def test_next_update_policy_distinguishes_general_coverage_and_market_semantics():
    baseline, _evidence, market, _watermarks = _incremental_baseline_and_evidence()
    limited = baseline.model_copy(
        update={
            "coverage": baseline.coverage.model_copy(
                update={
                    "claims": tuple(
                        item.model_copy(update={"status": CoverageStatus.LIMITED})
                        for item in baseline.coverage.claims
                    )
                }
            )
        }
    )
    limited_result = evaluate_next_update_policy(
        limited,
        instrument="6501.T",
        mode="shadow",
    )
    assert limited_result.reason == "anchor_coverage_incomplete"

    incompatible = baseline.model_copy(
        update={
            "delta": baseline.delta.model_copy(
                update={
                    "change_signals": (
                        ResearchChangeSignal(
                            kind=ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY,
                            domain="market",
                            record_id=market["record_id"],
                            current_version_id=market["version_id"],
                            requires_full_analysis=True,
                            detail="Adjustment semantics changed.",
                        ),
                    )
                }
            )
        }
    )
    incompatible_result = evaluate_next_update_policy(
        incompatible,
        instrument="6501.T",
        mode="shadow",
    )
    assert incompatible_result.reason == "incompatible_market_semantics"


def test_next_update_policy_rejects_future_scan_invalid_revision_and_missing_overlap():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    future_scan = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        item.model_copy(update={"scanned_end": date(2026, 7, 25)})
                        if item.source == "TDnet"
                        else item
                        for item in baseline.evidence_snapshot.source_watermarks
                    )
                }
            )
        }
    )
    assert (
        evaluate_next_update_policy(future_scan, instrument="6501.T", mode="shadow").reason
        == "anchor_coverage_incomplete"
    )

    invalid = baseline.model_copy(update={"cutoff": date(2026, 7, 23)})
    assert (
        evaluate_next_update_policy(invalid, instrument="6501.T", mode="shadow").reason
        == "invalid_revision"
    )

    candidate = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    ).candidate
    assert candidate is not None
    missing_overlap = candidate.model_copy(
        update={
            "evidence_snapshot": candidate.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        item.model_copy(update={"baseline_cutoff": None, "overlap_start": None})
                        for item in candidate.evidence_snapshot.source_watermarks
                    )
                }
            )
        }
    )
    assert (
        evaluate_next_update_policy(
            missing_overlap, instrument="6501.T", mode="experimental"
        ).reason
        == "legacy_anchor_coverage_unproven"
    )

    wrong_baseline = candidate.model_copy(
        update={
            "evidence_snapshot": candidate.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": tuple(
                        item.model_copy(update={"baseline_cutoff": date(2026, 7, 1)})
                        for item in candidate.evidence_snapshot.source_watermarks
                    )
                }
            )
        }
    )
    assert (
        evaluate_next_update_policy(wrong_baseline, instrument="6501.T", mode="experimental").reason
        == "legacy_anchor_coverage_unproven"
    )


def test_next_update_policy_rejects_mixed_current_and_future_source_watermarks():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    tdnet = next(
        item for item in baseline.evidence_snapshot.source_watermarks if item.source == "TDnet"
    )
    mixed = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": (
                        *baseline.evidence_snapshot.source_watermarks,
                        tdnet.model_copy(update={"scanned_end": date(2026, 7, 25)}),
                    )
                }
            )
        }
    )

    result = evaluate_next_update_policy(
        mixed,
        instrument="6501.T",
        mode="shadow",
    )

    assert result.reason == "anchor_coverage_incomplete"


def test_next_update_policy_accepts_nested_contiguous_source_watermarks():
    baseline, _evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    edinet = next(
        item for item in baseline.evidence_snapshot.source_watermarks if item.source == "EDINET"
    )
    nested_intervals = (
        edinet.model_copy(
            update={"scanned_start": date(2026, 7, 1), "scanned_end": date(2026, 7, 10)}
        ),
        edinet.model_copy(
            update={"scanned_start": date(2026, 7, 2), "scanned_end": date(2026, 7, 3)}
        ),
        edinet.model_copy(
            update={"scanned_start": date(2026, 7, 9), "scanned_end": date(2026, 7, 24)}
        ),
    )
    nested = baseline.model_copy(
        update={
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": (
                        *nested_intervals,
                        *(
                            item
                            for item in baseline.evidence_snapshot.source_watermarks
                            if item.source != "EDINET"
                        ),
                    )
                }
            )
        }
    )

    result = evaluate_next_update_policy(
        nested,
        instrument="6501.T",
        mode="shadow",
    )

    assert result.policy == "incremental_allowed"


def test_deterministic_incremental_gates_propose_quiet_candidate():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
        evidence,
    )

    assert result.escalation_reason is None
    assert result.candidate is not None
    assert result.candidate.change_conclusion is ResearchChangeConclusion.NO_MATERIAL_CHANGE
    assert result.candidate.execution_strategy.value == "incremental"
    assert result.candidate.coverage.supports_no_material_change is True
    assert {
        item.requirement
        for item in result.candidate.coverage.domains
        if item.source == "Google News"
    } == {CoverageRequirement.ADVISORY}


def test_experimental_nmc_validation_fails_closed_for_coverage_or_semantic_drift():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
        evidence,
    )
    candidate = result.candidate
    assert candidate is not None
    assert validate_experimental_nmc_candidate(baseline, candidate) is None

    incomplete = candidate.model_copy(
        update={
            "coverage": candidate.coverage.model_copy(update={"supports_no_material_change": False})
        }
    )
    changed = candidate.model_copy(
        update={
            "current_state": candidate.current_state.model_copy(
                update={
                    "opinion": candidate.current_state.opinion.model_copy(
                        update={"thesis": "A model attempted to rewrite the thesis."}
                    )
                }
            )
        }
    )
    material_signal = candidate.model_copy(
        update={
            "delta": candidate.delta.model_copy(
                update={
                    "change_signals": (
                        ResearchChangeSignal(
                            kind=ResearchChangeKind.MARKET_BOUNDARY_CROSSING,
                            domain="market",
                            record_id="jquants-market:6501.T",
                            requires_full_analysis=True,
                            detail="A thesis-relevant threshold was crossed.",
                        ),
                    )
                }
            )
        }
    )

    assert (
        validate_experimental_nmc_candidate(baseline, incomplete)
        is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    )
    assert (
        validate_experimental_nmc_candidate(baseline, changed)
        is IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
    )
    assert (
        validate_experimental_nmc_candidate(baseline, material_signal)
        is IncrementalEscalationReason.THRESHOLD_CROSSING
    )


def test_unqualified_incremental_head_cannot_claim_bounded_next_update():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
        evidence,
    )
    candidate = result.candidate
    assert candidate is not None

    policy = evaluate_next_update_policy(
        candidate,
        instrument="6501.T",
        mode="experimental",
    )

    assert policy.policy == "full_required"
    assert policy.reason is NextUpdateReason.LEGACY_ANCHOR_COVERAGE_UNPROVEN


@pytest.mark.parametrize(
    "audit_update",
    [
        {"transition_coverage": None},
        {"escalation_reason": "coverage_incomplete"},
    ],
)
def test_experimental_reaffirmation_requires_consistent_complete_transition_audit(
    audit_update,
):
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
        evidence,
    )
    candidate = result.candidate
    assert candidate is not None
    assert result.transition_coverage is not None
    valid_audit = ResearchUpdateAudit(
        mode="experimental",
        candidate=ResearchUpdateCandidate(
            change_conclusion="no_material_change",
            coverage=candidate.coverage.model_dump(mode="json"),
            update_summary=candidate.update_summary.model_dump(mode="json"),
            evidence_snapshot=candidate.evidence_snapshot.model_dump(mode="json"),
        ),
        transition_coverage=result.transition_coverage.model_dump(mode="json"),
        baseline_information_frontier=result.transition_coverage.anchor_frontier,
        authoritative_strategy="incremental",
        comparison="not_applicable",
    )
    audit = valid_audit.model_copy(update=audit_update)
    inconsistent = bind_information_frontier(
        candidate.model_copy(update={"research_update_audit": audit}),
        result.transition_coverage.update_frontier,
    )

    assert inconsistent.coverage.anchor_qualification is not None
    assert inconsistent.coverage.anchor_qualification.is_forward_research_anchor is False
    policy = evaluate_next_update_policy(
        inconsistent,
        instrument="6501.T",
        mode="experimental",
    )
    assert policy.policy == "full_required"
    assert policy.reason is NextUpdateReason.ANCHOR_COVERAGE_INCOMPLETE

    forged_transition = valid_audit.transition_coverage.model_copy(
        update={
            "anchor_frontier": valid_audit.baseline_information_frontier
            - timedelta(seconds=1)
        }
    )
    forged = bind_information_frontier(
        candidate.model_copy(
            update={
                "research_update_audit": valid_audit.model_copy(
                    update={"transition_coverage": forged_transition}
                )
            }
        ),
        result.transition_coverage.update_frontier,
    )
    assert forged.coverage.anchor_qualification is not None
    assert forged.coverage.anchor_qualification.is_forward_research_anchor is False


class _SemanticInvoker:
    def __init__(self, owner: _SemanticLLM, response: Any):
        self.owner = owner
        self.response = response

    def invoke(self, prompt: str, config: Any = None) -> Any:
        del config
        self.owner.prompts.append(prompt)
        return self.response


class _SemanticLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, *responses: Any):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def with_structured_output(self, _schema: Any, **kwargs: Any) -> _SemanticInvoker:
        assert kwargs["include_raw"] is True
        response = self.responses.pop(0)
        return _SemanticInvoker(self, response)


def _semantic_response(
    relationship: str,
    *,
    evidence_ref: str,
    claim_ids: tuple[str, ...] = (),
    question_ids: tuple[str, ...] = (),
    confidence: str | None = None,
    language: str = "en",
) -> dict[str, Any]:
    return {
        "raw": AIMessage(content=""),
        "parsed": {
            "language": language,
            "summary": "Bounded semantic assessment completed.",
            "relationships": [
                {
                    "evidence_refs": (evidence_ref,),
                    "relationship": relationship,
                    "suggested_claim_ids": claim_ids,
                    "suggested_question_ids": question_ids,
                    "suggested_claim_confidence": confidence,
                }
            ],
        },
        "parsing_error": None,
    }


@pytest.mark.parametrize(
    ("relationship", "escalates"),
    [
        ("support", False),
        ("weakening", True),
        ("contradiction", True),
        ("answering", True),
        ("reopening", True),
        ("irrelevance", False),
        ("uncertainty", True),
        ("potentially_material_novelty", True),
    ],
)
def test_semantic_change_assessment_supports_typed_relationships(
    relationship,
    escalates,
):
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    if relationship in {"answering", "reopening"}:
        question = ResearchQuestion(
            id="question_0123456789abcdef0123456789abcdef",
            question="Will margin recovery persist?",
            evidence_refs=(baseline.current_state.evidence_refs[0],),
        )
        baseline = baseline.model_copy(
            update={
                "current_state": baseline.current_state.model_copy(
                    update={"questions": (question,)}
                ),
                "coverage": baseline.coverage.model_copy(
                    update={
                        "questions": (
                            ResearchObjectCoverage(
                                object_id=question.id,
                                status=CoverageStatus.COMPLETE,
                            ),
                        )
                    }
                ),
            }
        )
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date="2026-07-25",
        analysts=("market",),
    )
    deterministic = assess_deterministic_update("revision-1", baseline, request, evidence)
    claim_ids = (
        (baseline.current_state.claims[0].id,)
        if relationship
        in {
            "support",
            "weakening",
            "contradiction",
        }
        else ()
    )
    question_ids = (
        (baseline.current_state.questions[0].id,)
        if (baseline.current_state.questions and relationship in {"answering", "reopening"})
        else ()
    )
    llm = _SemanticLLM(
        _semantic_response(
            relationship,
            evidence_ref=deterministic.candidate.delta.new_evidence_refs[0],
            claim_ids=claim_ids,
            question_ids=question_ids,
        )
    )

    result = assess_semantic_update(
        baseline,
        deterministic,
        llm,
    )

    assert result.semantic_assessment is not None
    assert result.semantic_assessment.relationships[0].relationship is SemanticChangeRelationship(
        relationship
    )
    assert (result.escalation_reason is not None) is escalates
    assert (result.candidate is not None) is not escalates


def test_semantic_change_assessment_rejects_ambiguous_identity_and_confidence_change():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))
    deterministic = assess_deterministic_update("revision-1", baseline, request, evidence)
    claim_id = baseline.current_state.claims[0].id
    changed_confidence = (
        "low" if baseline.current_state.claims[0].confidence is ClaimConfidence.HIGH else "high"
    )

    ambiguous = assess_semantic_update(
        baseline,
        deterministic,
        _SemanticLLM(
            _semantic_response(
                "support",
                evidence_ref=deterministic.candidate.delta.new_evidence_refs[0],
                claim_ids=(claim_id, "claim_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            )
        ),
    )
    confidence = assess_semantic_update(
        baseline,
        deterministic,
        _SemanticLLM(
            _semantic_response(
                "support",
                evidence_ref=deterministic.candidate.delta.new_evidence_refs[0],
                claim_ids=(claim_id,),
                confidence=changed_confidence,
            )
        ),
    )

    assert ambiguous.escalation_reason is IncrementalEscalationReason.AMBIGUOUS_IDENTITY
    assert confidence.escalation_reason is IncrementalEscalationReason.CONFIDENCE_CHANGE


def test_semantic_change_assessment_rejects_cross_item_identity_ambiguity():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    second_claim = baseline.current_state.claims[0].model_copy(
        update={
            "id": "claim_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "statement": "A second valid Claim.",
        }
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"claims": (*baseline.current_state.claims, second_claim)}
            ),
            "coverage": baseline.coverage.model_copy(
                update={
                    "claims": (
                        *baseline.coverage.claims,
                        ResearchObjectCoverage(
                            object_id=second_claim.id,
                            status=CoverageStatus.COMPLETE,
                        ),
                    )
                }
            ),
        }
    )
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))
    deterministic = assess_deterministic_update("revision-1", baseline, request, evidence)
    new_ref = deterministic.candidate.delta.new_evidence_refs[0]
    llm = _SemanticLLM(
        {
            "raw": AIMessage(content=""),
            "parsed": {
                "language": "en",
                "summary": "One Evidence item was assigned twice.",
                "relationships": [
                    {
                        "evidence_refs": [new_ref],
                        "relationship": "support",
                        "suggested_claim_ids": [baseline.current_state.claims[0].id],
                    },
                    {
                        "evidence_refs": [new_ref],
                        "relationship": "support",
                        "suggested_claim_ids": [second_claim.id],
                    },
                ],
            },
            "parsing_error": None,
        }
    )

    result = assess_semantic_update(baseline, deterministic, llm)

    assert result.escalation_reason is IncrementalEscalationReason.AMBIGUOUS_IDENTITY


def test_semantic_change_assessment_excludes_prior_research_disguised_as_evidence():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    prior_research = EvidenceItem.create(
        source="Prior Research",
        evidence_type="Research Artifact",
        requested_date=CUTOFF,
        effective_date=CUTOFF,
        content="PRIVATE PRIOR CONCLUSION MUST NOT APPEAR",
        provenance={"content_kind": "prior_research"},
    )
    claim = baseline.current_state.claims[0].model_copy(
        update={
            "evidence_refs": (*baseline.current_state.claims[0].evidence_refs, prior_research.ref)
        }
    )
    claims = (claim, *baseline.current_state.claims[1:])
    baseline_bundle = baseline.evidence_snapshot.bundle.model_copy(
        update={"items": (*baseline.evidence_snapshot.bundle.items, prior_research)}
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={
                    "claims": claims,
                    "evidence_refs": (*baseline.current_state.evidence_refs, prior_research.ref),
                }
            ),
            "evidence_snapshot": baseline.evidence_snapshot.model_copy(
                update={
                    "bundle": baseline_bundle,
                    "lineage": (
                        *baseline.evidence_snapshot.lineage,
                        EvidenceSnapshotItem(
                            evidence_ref=prior_research.ref,
                            lineage="new",
                        ),
                    ),
                }
            ),
        }
    )
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))
    deterministic = assess_deterministic_update("revision-1", baseline, request, evidence)
    llm = _SemanticLLM(
        _semantic_response(
            "support",
            evidence_ref=deterministic.candidate.delta.new_evidence_refs[0],
            claim_ids=(claim.id,),
        )
    )

    result = assess_semantic_update(baseline, deterministic, llm)

    assert result.candidate is not None
    assert prior_research.ref not in llm.prompts[0]
    assert "PRIVATE PRIOR CONCLUSION" not in llm.prompts[0]


def test_semantic_change_assessment_rejects_new_prior_research_without_model_call():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    disguised = EvidenceItem.create(
        source="Prior Research",
        evidence_type="Research Artifact",
        requested_date=date(2026, 7, 25),
        effective_date=date(2026, 7, 25),
        content="PRIVATE NEW CONCLUSION MUST NOT APPEAR",
        provenance={
            **evidence.items[0].provenance,
            "content_kind": "prior_research",
        },
    )
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))
    deterministic = assess_deterministic_update(
        "revision-1",
        baseline,
        request,
        EvidenceBundle(
            instrument="6501.T",
            analysis_date=date(2026, 7, 25),
            items=(disguised,),
        ),
    )
    llm = _SemanticLLM()

    result = assess_semantic_update(baseline, deterministic, llm)

    assert result.escalation_reason is IncrementalEscalationReason.SCHEMA_INVALID
    assert llm.prompts == []


def test_quiet_candidate_reaffirms_only_relevant_research_objects():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    inactive = baseline.current_state.claims[0].model_copy(
        update={
            "id": "claim_cccccccccccccccccccccccccccccccc",
            "standing": ClaimStanding.RETIRED,
        }
    )
    retired_question = ResearchQuestion(
        id="question_dddddddddddddddddddddddddddddddd",
        question="A retired question?",
        status=QuestionStatus.RETIRED,
        evidence_refs=(baseline.current_state.evidence_refs[0],),
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={
                    "claims": (*baseline.current_state.claims, inactive),
                    "questions": (retired_question,),
                }
            ),
            "coverage": baseline.coverage.model_copy(
                update={
                    "claims": (
                        *baseline.coverage.claims,
                        ResearchObjectCoverage(
                            object_id=inactive.id,
                            status=CoverageStatus.COMPLETE,
                        ),
                    ),
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=retired_question.id,
                            status=CoverageStatus.COMPLETE,
                        ),
                    ),
                }
            ),
        }
    )
    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.candidate is not None
    assert {item.object_id for item in result.candidate.delta.claims} == {
        item.id for item in baseline.current_state.claims if item.standing is ClaimStanding.ACTIVE
    }
    assert result.candidate.delta.questions == ()


def test_semantic_change_assessment_repairs_once_then_escalates_invalid_output():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))
    deterministic = assess_deterministic_update("revision-1", baseline, request, evidence)
    claim_id = baseline.current_state.claims[0].id
    invalid = {
        "raw": AIMessage(content="{}"),
        "parsed": None,
        "parsing_error": ValueError("invalid"),
    }
    repaired_llm = _SemanticLLM(
        invalid,
        _semantic_response(
            "support",
            evidence_ref=deterministic.candidate.delta.new_evidence_refs[0],
            claim_ids=(claim_id,),
        ),
    )

    repaired = assess_semantic_update(baseline, deterministic, repaired_llm)
    failed_llm = _SemanticLLM(invalid, invalid)
    failed = assess_semantic_update(baseline, deterministic, failed_llm)

    assert repaired.candidate is not None
    assert len(repaired_llm.prompts) == 2
    assert failed.escalation_reason is IncrementalEscalationReason.SEMANTIC_OUTPUT_INVALID
    assert len(failed_llm.prompts) == 2


def test_semantic_change_assessment_preserves_language_and_excludes_research_artifacts():
    baseline, evidence, _market, _watermarks = _incremental_baseline_and_evidence()
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"language": "ja"}),
            "update_summary": baseline.update_summary.model_copy(update={"language": "ja"}),
        }
    )
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date="2026-07-25",
        analysts=("market",),
        output_language="ja",
    )
    deterministic = assess_deterministic_update("revision-1", baseline, request, evidence)
    llm = _SemanticLLM(
        _semantic_response(
            "support",
            evidence_ref=deterministic.candidate.delta.new_evidence_refs[0],
            claim_ids=(baseline.current_state.claims[0].id,),
            language="ja",
        )
    )

    result = assess_semantic_update(baseline, deterministic, llm)

    assert result.candidate is not None
    assert result.candidate.update_summary.language == "ja"
    assert result.semantic_assessment.language == "ja"
    prompt = llm.prompts[0]
    assert "Current Research State" in prompt
    assert "new_evidence" in prompt
    assert "old reports" not in prompt.casefold()
    assert "deliberation" not in prompt.casefold()
    assert "prior research" not in prompt.casefold()


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("corrected", IncrementalEscalationReason.SOURCE_CORRECTION),
        ("withdrawn", IncrementalEscalationReason.SOURCE_WITHDRAWAL),
    ],
)
def test_deterministic_incremental_gates_escalate_disclosure_integrity_changes(
    status,
    reason,
):
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    changed = _source_record(f"edinet:{status}", status=status)
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market, changed],
        candidate_watermarks=watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.candidate is None
    assert result.escalation_reason is reason


def test_deterministic_incremental_gates_escalate_missing_required_coverage():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    missing_tdnet = [item for item in watermarks if item["source"] != "TDnet"]
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=missing_tdnet,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE


def test_deterministic_incremental_gates_escalate_stale_required_window():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    assert result.coverage is not None
    assert "update cutoff" in " ".join(result.coverage.limitations)


def test_deterministic_incremental_gates_escalate_window_past_cutoff():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    future_windows = [{**item, "scanned_end": "2026-07-26"} for item in watermarks]
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[market],
        candidate_watermarks=future_windows,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.escalation_reason is IncrementalEscalationReason.COVERAGE_INCOMPLETE
    assert result.coverage is not None
    assert "update cutoff" in " ".join(result.coverage.limitations)


def test_deterministic_incremental_gates_escalate_incompatible_market_semantics():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    changed = {
        **market,
        "version_id": "market:v2",
        "adjustment": "raw",
        "observation_value": 96.0,
    }
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[changed],
        candidate_watermarks=watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.escalation_reason is IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS


def test_deterministic_incremental_gates_escalate_market_threshold_crossing():
    baseline, _evidence, market, watermarks = _incremental_baseline_and_evidence()
    evidence_ref = baseline.current_state.evidence_refs[0]
    boundary = MarketReferenceLevel(
        label="Thesis reference",
        value=100.0,
        measurement_kind="currency",
        unit="JPY",
        as_of_date=CUTOFF,
        interpretation="Crossing changes the thesis envelope.",
        evidence_refs=(evidence_ref,),
        date_evidence_refs=(evidence_ref,),
        basis="interpreted",
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={
                    "market_reference_levels": (boundary,),
                }
            )
        }
    )
    changed = {**market, "version_id": "market:v2", "observation_value": 101.0}
    _, evidence, _, _ = _incremental_baseline_and_evidence(
        candidate_records=[changed],
        candidate_watermarks=watermarks,
    )

    result = assess_deterministic_update(
        "revision-1",
        baseline,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        evidence,
    )

    assert result.escalation_reason is IncrementalEscalationReason.THRESHOLD_CROSSING


def test_revision_snapshot_retains_disclosure_versions_and_source_coverage():
    execution = _with_disclosure_metadata(
        _execution("6501.T"),
        records=[_source_record("edinet:S100ROOT")],
        watermarks=[
            _watermark("EDINET"),
            _watermark("TDnet", status="limited", limitations=("archive limited",)),
            _watermark("Google News"),
        ],
    )

    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        execution,
    )

    assert draft.evidence_snapshot.source_records[0].version_id == "edinet:S100ROOT"
    assert draft.evidence_snapshot.source_record_lineage[0].lineage == "new"
    sources = {item.source: item for item in draft.coverage.domains if item.source}
    assert sources["TDnet"].status.value == "limited"
    assert sources["TDnet"].requirement is CoverageRequirement.REQUIRED
    assert sources["Google News"].requirement is CoverageRequirement.ADVISORY
    assert draft.coverage.supports_no_material_change is False


def test_revision_deduplicates_one_source_version_observed_by_multiple_tools():
    metadata = {
        "source_records": [
            {
                **_source_record("jquants-fundamental:stable"),
                "source": "J-Quants fundamentals",
                "record_id": "jquants-fundamental:6501:202607240001",
                "record_kind": "fundamental",
                "comparison_key": "6501:FY:2026-03-31",
            }
        ],
        "source_watermarks": [_watermark("J-Quants fundamentals")],
    }
    execution = _execution("6501.T")
    first = execution.evidence.items[0].model_copy(update={"provenance": metadata})
    second = EvidenceItem.create(
        source="J-Quants fundamentals",
        evidence_type="income statement",
        requested_date=CUTOFF,
        effective_date=CUTOFF,
        content="The same official summary rendered as an income statement.",
        provenance=metadata,
    )
    execution = execution.__class__(
        state=execution.state,
        evidence=EvidenceBundle(instrument="6501.T", analysis_date=CUTOFF, items=(first, second)),
        reports=execution.reports,
        decision=execution.decision,
    )

    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        execution,
    )

    assert [item.version_id for item in draft.evidence_snapshot.source_records] == [
        "jquants-fundamental:stable"
    ]


def test_revision_preserves_disjoint_source_watermark_intervals():
    execution = _with_disclosure_metadata(
        _execution("6501.T"),
        records=[],
        watermarks=[
            {
                **_watermark("EDINET"),
                "scanned_start": "2026-07-01",
                "scanned_end": "2026-07-05",
            }
        ],
    )
    second = EvidenceItem.create(
        source="EDINET",
        evidence_type="disclosure coverage",
        requested_date=CUTOFF,
        effective_date=CUTOFF,
        content="Second scan.",
        provenance={
            "source_watermarks": [
                {
                    **_watermark("EDINET"),
                    "scanned_start": "2026-07-20",
                    "scanned_end": "2026-07-24",
                }
            ]
        },
    )
    execution = execution.__class__(
        state=execution.state,
        evidence=EvidenceBundle(
            instrument="6501.T",
            analysis_date=CUTOFF,
            items=(*execution.evidence.items, second),
        ),
        reports=execution.reports,
        decision=execution.decision,
    )

    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        execution,
    )

    assert [
        (item.scanned_start, item.scanned_end)
        for item in draft.evidence_snapshot.source_watermarks
        if item.source == "EDINET"
    ] == [
        (date(2026, 7, 1), date(2026, 7, 5)),
        (date(2026, 7, 20), date(2026, 7, 24)),
    ]
    edinet = next(item for item in draft.coverage.domains if item.source == "EDINET")
    assert edinet.status is CoverageStatus.LIMITED
    assert "gap" in edinet.limitations[0].lower()
    assert draft.coverage.supports_no_material_change is False


def test_full_update_preserves_corrected_versions_with_overlap_lineage():
    baseline_execution = _with_disclosure_metadata(
        _execution("6501.T"),
        records=[_source_record("edinet:S100ROOT")],
        watermarks=[_watermark("EDINET")],
    )
    candidate_execution = _with_disclosure_metadata(
        _execution("6501.T"),
        records=[
            _source_record("edinet:S100ROOT"),
            _source_record(
                "edinet:S100CORRECTION",
                status="corrected",
                replaces="edinet:S100ROOT",
            ),
        ],
        watermarks=[_watermark("EDINET")],
    )
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        baseline_execution,
    )
    candidate = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=date(2026, 7, 25), analysts=("market",)),
        candidate_execution,
    )

    updated = assemble_full_update("revision-1", baseline, candidate)
    audit_coverage = ResearchUpdateCoverageAttestation.model_validate(
        updated.coverage.model_dump(mode="python", exclude={"anchor_qualification"})
    )
    updated = updated.model_copy(
        update={
            "research_update_audit": ResearchUpdateAudit(
                candidate=ResearchUpdateCandidate(
                    change_conclusion="no_material_change",
                    coverage=audit_coverage,
                    update_summary=ResearchUpdateSummaryContract.model_validate(
                        updated.update_summary.model_dump(mode="python")
                    ),
                    evidence_snapshot=updated.evidence_snapshot.model_dump(mode="python"),
                ),
                coverage=audit_coverage,
                comparison="inconclusive",
            )
        }
    )

    assert {item.version_id for item in updated.evidence_snapshot.source_records} == {
        "edinet:S100ROOT",
        "edinet:S100CORRECTION",
    }
    lineage = {item.version_id: item for item in updated.evidence_snapshot.source_record_lineage}
    assert lineage["edinet:S100ROOT"].lineage == "inherited"
    assert lineage["edinet:S100ROOT"].observed_in_execution is True
    assert lineage["edinet:S100CORRECTION"].lineage == "new"
    watermark = updated.evidence_snapshot.source_watermarks[0]
    assert watermark.baseline_cutoff == CUTOFF
    assert watermark.overlap_start == date(2026, 7, 1)

    revision = ResearchRevision(
        **updated.model_dump(),
        id="revision-2",
        chain_id="chain-1",
        sequence=2,
        predecessor_revision_id="revision-1",
        created_at="2026-07-25T00:00:00Z",
    )
    chain = ResearchChain(
        id="chain-1",
        instrument="6501.T",
        is_primary=True,
        current_revision_id=revision.id,
        created_at="2026-07-24T00:00:00Z",
        updated_at="2026-07-25T00:00:00Z",
    )

    exported = render_revision_export_markdown(RevisionExport(chain=chain, revision=revision))

    assert "Revision role: update" in exported
    assert "Execution strategy: full" in exported
    assert "Change conclusion:" in exported
    assert "counted as neither agreement nor disagreement" in exported
    assert "## Source Watermarks" in exported
    assert "EDINET: 2026-07-01 to 2026-07-24" in exported
    assert "## Source Record Versions" in exported
    assert "edinet:S100CORRECTION" in exported
    assert "corrected" in exported


def test_full_update_classifies_fundamental_restatement_and_scope_change():
    base_record = {
        **_source_record("jquants-fundamental:v1"),
        "source": "J-Quants fundamentals",
        "record_id": "jquants-fundamental:6501:FY:2026-03-31",
        "record_kind": "fundamental",
        "native_record_id": "202607240001",
        "comparison_key": "6501:FY:2026-03-31",
        "accounting_scope": "consolidated:ifrs",
    }
    changed_record = {
        **base_record,
        "version_id": "jquants-fundamental:v2",
        "status": "corrected",
        "change_hint": "restatement",
        "replaces_version_id": "jquants-fundamental:v1",
    }
    scope_record = {
        **changed_record,
        "version_id": "jquants-fundamental:v3",
        "accounting_scope": "non-consolidated:japanese-gaap",
        "change_hint": "accounting_scope_change",
        "replaces_version_id": "jquants-fundamental:v2",
    }
    new_filing = {
        **base_record,
        "record_id": "jquants-fundamental:6501:202607250001",
        "version_id": "jquants-fundamental:new-period",
        "comparison_key": "6501:1Q:2026-06-30",
        "change_hint": "new_filing",
        "replaces_version_id": None,
    }
    complete = [
        _watermark("EDINET"),
        _watermark("TDnet"),
        _watermark("J-Quants fundamentals"),
    ]
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _with_disclosure_metadata(_execution("6501.T"), records=[base_record], watermarks=complete),
    )
    candidate = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=date(2026, 7, 25), analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[base_record, changed_record, scope_record, new_filing],
            watermarks=complete,
        ),
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert [item.kind for item in updated.delta.change_signals] == [
        ResearchChangeKind.FUNDAMENTAL_RESTATEMENT,
        ResearchChangeKind.ACCOUNTING_SCOPE_CHANGE,
        ResearchChangeKind.NEW_FUNDAMENTAL_FILING,
    ]
    assert all(item.requires_full_analysis for item in updated.delta.change_signals)
    revision = ResearchRevision(
        **updated.model_dump(),
        id="revision-fundamentals",
        chain_id="chain-1",
        sequence=2,
        predecessor_revision_id="revision-1",
        created_at="2026-07-25T00:00:00Z",
    )
    chain = ResearchChain(
        id="chain-1",
        instrument="6501.T",
        is_primary=True,
        current_revision_id=revision.id,
        created_at="2026-07-24T00:00:00Z",
        updated_at="2026-07-25T00:00:00Z",
    )
    exported = render_revision_export_markdown(RevisionExport(chain=chain, revision=revision))
    assert "## Fundamental and Market Change Signals" in exported
    assert "fundamental_restatement" in exported
    assert "accounting_scope_change" in exported
    assert "native record: 202607240001" in exported
    assert "fallback: false" in exported


@pytest.mark.parametrize(
    ("candidate_updates", "expected"),
    [
        (
            {"status": "corrected", "change_hint": "correction"},
            ResearchChangeKind.FUNDAMENTAL_CORRECTION,
        ),
        (
            {"status": "published", "change_hint": "unclassifiable"},
            ResearchChangeKind.UNCLASSIFIABLE_FUNDAMENTAL_CHANGE,
        ),
    ],
)
def test_full_update_classifies_other_fundamental_snapshot_differences(
    candidate_updates,
    expected,
):
    baseline_record = {
        **_source_record("fundamental:v1"),
        "source": "J-Quants fundamentals",
        "record_id": "jquants-disclosure:1",
        "record_kind": "fundamental",
        "comparison_key": "6501:FY:2026-03-31",
        "accounting_scope": "consolidated:ifrs",
    }
    candidate_record = {
        **baseline_record,
        "version_id": "fundamental:v2",
        "record_id": "jquants-disclosure:2",
        "replaces_version_id": "fundamental:v1",
        **candidate_updates,
    }
    watermarks = [
        _watermark("EDINET"),
        _watermark("TDnet"),
        _watermark("J-Quants fundamentals"),
    ]
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("fundamentals",)),
        _with_disclosure_metadata(
            _execution("6501.T"), records=[baseline_record], watermarks=watermarks
        ),
    )
    candidate = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=date(2026, 7, 25),
            analysts=("fundamentals",),
        ),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[baseline_record, candidate_record],
            watermarks=watermarks,
        ),
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.delta.change_signals[0].kind is expected
    assert updated.delta.change_signals[0].record_id == "jquants-disclosure:2"
    assert updated.coverage.supports_no_material_change is False


def test_full_update_fails_closed_for_market_semantic_incompatibility_and_records_unchanged():
    def market_record(version: str, adjustment: str):
        return {
            **_source_record(version),
            "source": "J-Quants adjusted OHLCV",
            "record_id": "jquants-market:6501",
            "record_kind": "market",
            "adjustment": adjustment,
            "observation_value": 95.0,
            "unit": "JPY",
        }

    watermarks = [
        _watermark("EDINET"),
        _watermark("TDnet"),
        _watermark("J-Quants adjusted OHLCV"),
    ]

    def draft(cutoff, record):
        return assemble_full_revision(
            AnalysisRequest(ticker="6501.T", analysis_date=cutoff, analysts=("market",)),
            _with_disclosure_metadata(
                _execution("6501.T"), records=[record], watermarks=watermarks
            ),
        )

    baseline_record = market_record("market:v1", "J-Quants adjusted OHLCV v2")
    baseline = draft(CUTOFF, baseline_record)
    unchanged = assemble_full_update(
        "revision-1", baseline, draft(date(2026, 7, 25), baseline_record)
    )
    incompatible = assemble_full_update(
        "revision-1",
        baseline,
        draft(date(2026, 7, 25), market_record("market:v2", "raw OHLCV")),
    )

    assert unchanged.delta.change_signals[0].kind is ResearchChangeKind.UNCHANGED_OBSERVATION
    assert unchanged.delta.change_signals[0].requires_full_analysis is False
    assert incompatible.delta.change_signals[0].kind is (
        ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY
    )
    assert incompatible.coverage.supports_no_material_change is False


def test_full_update_does_not_use_baseline_market_watermark_for_missing_current_scan():
    market_record = {
        **_source_record("market:v1"),
        "source": "J-Quants adjusted OHLCV",
        "record_id": "jquants-market:6501",
        "record_kind": "market",
        "adjustment": "J-Quants adjusted OHLCV v2",
        "observation_value": 95.0,
        "unit": "JPY",
    }
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[market_record],
            watermarks=[
                _watermark("EDINET"),
                _watermark("TDnet"),
                _watermark("J-Quants adjusted OHLCV"),
            ],
        ),
    )
    candidate = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=date(2026, 7, 25), analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"),
            records=[],
            watermarks=[_watermark("EDINET"), _watermark("TDnet")],
        ),
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    market = next(
        item for item in updated.coverage.domains if item.source == "J-Quants adjusted OHLCV"
    )
    assert market.status is CoverageStatus.UNAVAILABLE
    assert updated.coverage.supports_no_material_change is False
    assert updated.change_conclusion is not ResearchChangeConclusion.NO_MATERIAL_CHANGE


def test_full_update_distinguishes_market_movement_from_boundary_crossing():
    def market_record(version: str, value: float):
        return {
            **_source_record(version),
            "source": "J-Quants adjusted OHLCV",
            "record_id": "jquants-market:6501",
            "record_kind": "market",
            "adjustment": "J-Quants adjusted OHLCV v2",
            "observation_value": value,
            "unit": "JPY",
        }

    complete = [_watermark("EDINET"), _watermark("TDnet"), _watermark("J-Quants adjusted OHLCV")]
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _with_disclosure_metadata(
            _execution("6501.T"), records=[market_record("market:v1", 95.0)], watermarks=complete
        ),
    )
    boundary = MarketReferenceLevel(
        label="Thesis reference",
        value=100.0,
        measurement_kind="currency",
        unit="JPY",
        as_of_date=CUTOFF,
        interpretation="Crossing changes the thesis envelope.",
        evidence_refs=(REF,),
        date_evidence_refs=(REF,),
        basis="interpreted",
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"market_reference_levels": (boundary,)}
            )
        }
    )

    def update(value: float):
        candidate = assemble_full_revision(
            AnalysisRequest(ticker="6501.T", analysis_date=date(2026, 7, 25), analysts=("market",)),
            _with_disclosure_metadata(
                _execution("6501.T"),
                records=[market_record(f"market:{value}", value)],
                watermarks=complete,
            ),
        )
        return assemble_full_update("revision-1", baseline, candidate)

    ordinary = update(99.0)
    crossed = update(101.0)

    assert ordinary.delta.change_signals[0].kind is ResearchChangeKind.ORDINARY_MARKET_MOVE
    assert ordinary.delta.change_signals[0].requires_full_analysis is False
    assert crossed.delta.change_signals[0].kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING
    assert crossed.delta.change_signals[0].boundary_label == "Thesis reference"
    assert crossed.delta.change_signals[0].requires_full_analysis is True


def test_inherited_correction_does_not_permanently_block_quiet_reassessment():
    def single_claim(draft, cutoff):
        claim = draft.current_state.claims[0]
        state = draft.current_state.model_copy(
            update={
                "cutoff": cutoff,
                "claims": (claim,),
                "questions": (),
                "scenarios": tuple(
                    item.model_copy(
                        update={
                            "cutoff": cutoff,
                            "assumption_claim_ids": (claim.id,),
                        }
                    )
                    for item in draft.current_state.scenarios
                ),
                "opinion": draft.current_state.opinion.model_copy(
                    update={"primary_claim_ids": (claim.id,)}
                ),
                "risks": (),
                "catalysts": (),
                "invalidation_conditions": (),
            }
        )
        claim_coverage = next(item for item in draft.coverage.claims if item.object_id == claim.id)
        return draft.model_copy(
            update={
                "cutoff": cutoff,
                "current_state": state,
                "coverage": draft.coverage.model_copy(
                    update={"claims": (claim_coverage,), "questions": ()}
                ),
            }
        )

    corrected_records = [
        _source_record("edinet:S100ROOT"),
        _source_record(
            "edinet:S100CORRECTION",
            status="corrected",
            replaces="edinet:S100ROOT",
        ),
        {
            **_source_record("jquants-market:stable"),
            "source": "J-Quants adjusted OHLCV",
            "record_id": "jquants-market:6501",
            "record_kind": "market",
            "adjustment": "J-Quants adjusted OHLCV v2",
            "observation_value": 100.0,
            "unit": "JPY",
        },
    ]
    complete_watermarks = [
        _watermark("EDINET"),
        _watermark("TDnet", returned_records=0, reported_records=0),
        _watermark("J-Quants adjusted OHLCV"),
    ]
    baseline = single_claim(
        assemble_full_revision(
            AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
            _with_disclosure_metadata(
                _execution("6501.T"),
                records=corrected_records,
                watermarks=complete_watermarks,
            ),
        ),
        CUTOFF,
    )
    next_cutoff = date(2026, 7, 25)
    candidate = single_claim(
        assemble_full_revision(
            AnalysisRequest(ticker="6501.T", analysis_date=next_cutoff, analysts=("market",)),
            _with_disclosure_metadata(
                _execution("6501.T"),
                records=corrected_records,
                watermarks=complete_watermarks,
            ),
        ),
        next_cutoff,
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.coverage.supports_no_material_change is True
    assert updated.change_conclusion is ResearchChangeConclusion.NO_MATERIAL_CHANGE


def test_full_analysis_promotes_google_news_only_for_explicit_dependency():
    baseline_execution = _with_disclosure_metadata(
        _execution("6501.T"),
        records=[],
        watermarks=[_watermark("Google News", temporal_scope="live_only")],
    )
    question = "Does media coverage resolve the open catalyst?"
    baseline_execution = baseline_execution.__class__(
        state=baseline_execution.state,
        evidence=baseline_execution.evidence,
        reports=baseline_execution.reports,
        decision=baseline_execution.decision.model_copy(
            update={
                "unresolved_questions": (question,),
                "question_source_dependencies": (
                    ResearchQuestionSourceDependency(
                        question=question,
                        required_sources=("Google News",),
                    ),
                ),
            }
        ),
    )
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        baseline_execution,
    )
    google = next(item for item in baseline.coverage.domains if item.source == "Google News")
    assert google.requirement is CoverageRequirement.REQUIRED
    assert google.status.value == "limited"
    assert "not point-in-time" in google.limitations[0]
    assert baseline.coverage.supports_no_material_change is False


def test_full_analysis_claim_can_explicitly_require_google_news():
    execution = _with_disclosure_metadata(
        _execution("6501.T"),
        records=[],
        watermarks=[_watermark("Google News")],
    )
    evidence = execution.evidence.items[0].model_copy(update={"source": "Google News"})
    report = execution.reports["market"]
    report = report.model_copy(
        update={
            "key_claims": tuple(
                item.model_copy(update={"required_sources": ("Google News",)})
                for item in report.key_claims
            )
        }
    )
    execution = execution.__class__(
        state=execution.state,
        evidence=EvidenceBundle(
            instrument="6501.T",
            analysis_date=CUTOFF,
            items=(evidence,),
        ),
        reports={"market": report},
        decision=execution.decision,
    )

    draft = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        execution,
    )

    assert draft.current_state.claims[0].required_sources == ("Google News",)
    google = next(item for item in draft.coverage.domains if item.source == "Google News")
    assert google.requirement is CoverageRequirement.REQUIRED


def test_social_and_broad_news_remain_advisory_without_research_dependency():
    execution = _execution("6501.T")
    report = execution.reports["market"]
    execution = execution.__class__(
        state=execution.state,
        evidence=execution.evidence,
        reports={"market": report, "social": report, "news": report},
        decision=execution.decision,
    )

    draft = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=CUTOFF,
            analysts=("market", "social", "news"),
        ),
        execution,
    )

    requirements = {
        item.domain: item.requirement for item in draft.coverage.domains if item.source is None
    }
    assert requirements["market"] is CoverageRequirement.REQUIRED
    assert requirements["social"] is CoverageRequirement.ADVISORY
    assert requirements["news"] is CoverageRequirement.ADVISORY


def test_coverage_blocker_cannot_produce_no_material_change():
    def single_claim(draft, cutoff):
        claim = draft.current_state.claims[0]
        state = draft.current_state.model_copy(
            update={
                "cutoff": cutoff,
                "claims": (claim,),
                "questions": (),
                "scenarios": tuple(
                    item.model_copy(
                        update={
                            "cutoff": cutoff,
                            "assumption_claim_ids": (claim.id,),
                        }
                    )
                    for item in draft.current_state.scenarios
                ),
                "opinion": draft.current_state.opinion.model_copy(
                    update={"primary_claim_ids": (claim.id,)}
                ),
                "risks": (),
                "catalysts": (),
                "invalidation_conditions": (),
            }
        )
        claim_coverage = next(item for item in draft.coverage.claims if item.object_id == claim.id)
        return draft.model_copy(
            update={
                "cutoff": cutoff,
                "current_state": state,
                "coverage": draft.coverage.model_copy(
                    update={"claims": (claim_coverage,), "questions": ()}
                ),
            }
        )

    baseline = single_claim(
        assemble_full_revision(
            AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
            _execution("6501.T"),
        ),
        CUTOFF,
    )
    next_cutoff = date(2026, 7, 25)
    candidate = single_claim(
        assemble_full_revision(
            AnalysisRequest(ticker="6501.T", analysis_date=next_cutoff, analysts=("market",)),
            _with_disclosure_metadata(
                _execution("6501.T"),
                records=[],
                watermarks=[
                    _watermark(
                        "TDnet",
                        status="limited",
                        limitations=("archive limited",),
                    )
                ],
            ),
        ),
        next_cutoff,
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.delta.changed_sections == ()
    assert updated.coverage.supports_no_material_change is False
    assert updated.change_conclusion is ResearchChangeConclusion.INDETERMINATE
    assert updated.indeterminate_reason.value == "coverage_incomplete"


def test_full_update_does_not_reassign_ambiguous_claim_identity():
    baseline = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=CUTOFF,
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    duplicate = baseline.current_state.claims[0].model_copy(
        update={"id": "claim_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    )
    duplicate_coverage = baseline.coverage.claims[0].model_copy(update={"object_id": duplicate.id})
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"claims": (*baseline.current_state.claims, duplicate)}
            ),
            "coverage": baseline.coverage.model_copy(
                update={"claims": (*baseline.coverage.claims, duplicate_coverage)}
            ),
        }
    )
    candidate = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=date(2026, 7, 25),
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    candidate_id = candidate.current_state.claims[0].id

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.current_state.claims[0].id == candidate_id
    assert updated.delta.claims[0].identity_disposition.value == "ambiguous_new"


def test_full_update_applies_explicit_answered_question_as_material_change():
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _execution("6501.T"),
    )
    baseline_question = ResearchQuestion(
        id="question_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        question="Will orders remain durable?",
        status=QuestionStatus.OPEN,
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"questions": (baseline_question,)}
            ),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=baseline_question.id,
                            status="limited",
                        ),
                    )
                }
            ),
        }
    )
    candidate = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=date(2026, 7, 25),
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    candidate_question = ResearchQuestion(
        id="question_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        question=baseline_question.question,
        status=QuestionStatus.ANSWERED,
    )
    candidate = candidate.model_copy(
        update={
            "current_state": candidate.current_state.model_copy(
                update={"questions": (candidate_question,)}
            ),
            "coverage": candidate.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=candidate_question.id,
                            status="complete",
                        ),
                    )
                }
            ),
            "delta": candidate.delta.model_copy(
                update={
                    "question_disposition": QuestionDispositionAudit(
                        status="complete",
                        language="en",
                        dispositions=(
                            QuestionDispositionRecord(
                                baseline_question_id=baseline_question.id,
                                disposition="answered",
                                candidate_question_id=candidate_question.id,
                                evidence_refs=(candidate.current_state.evidence_refs[0],),
                                reason="Current Full Evidence answers the baseline Question.",
                            ),
                        ),
                    )
                }
            ),
        }
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.current_state.questions[0].id == baseline_question.id
    assert updated.delta.questions[0].change.value == "answered"
    assert updated.change_conclusion is ResearchChangeConclusion.MATERIAL_CHANGE
