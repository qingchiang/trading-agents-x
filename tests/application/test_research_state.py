from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from tests.application.test_service import _execution
from tradingagents.application.contracts import (
    AnalysisRequest,
    EvidenceBundle,
    EvidenceItem,
    ReportLanguage,
    ResearchQuestionSourceDependency,
    ResearchRating,
    ResearchScenarioKind,
)
from tradingagents.application.research import (
    ClaimConfidence,
    ClaimStanding,
    CoverageRequirement,
    CoverageStatus,
    CurrentResearchState,
    DecisionConfidence,
    DecisionRole,
    EpistemicKind,
    QuestionStatus,
    ResearchChain,
    ResearchClaim,
    ResearchObjectCoverage,
    ResearchOpinion,
    ResearchQuestion,
    ResearchRevision,
    ResearchRevisionOutcome,
    ResearchScenarioState,
    RevisionExport,
    ScenarioLikelihood,
    assemble_full_revision,
    assemble_full_update,
    render_revision_export_markdown,
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
    assert draft.coverage.domains[0].domain == "market"
    assert draft.evidence_snapshot.bundle.digest == execution.evidence.digest
    assert {item.lineage for item in draft.evidence_snapshot.lineage} == {"new"}
    assert draft.update_summary.language == "ja"
    missing_sources = {item.source for item in draft.coverage.domains if item.source}
    assert missing_sources == {"EDINET", "TDnet"}
    assert draft.coverage.supports_no_material_change is False


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
):
    return {
        "source": source,
        "scanned_start": "2026-07-01",
        "scanned_end": "2026-07-24",
        "status": status,
        "temporal_scope": temporal_scope,
        "limitations": limitations,
        "returned_records": 1,
        "reported_records": 1,
    }


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
        AnalysisRequest(
            ticker="6501.T", analysis_date=date(2026, 7, 25), analysts=("market",)
        ),
        candidate_execution,
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert {item.version_id for item in updated.evidence_snapshot.source_records} == {
        "edinet:S100ROOT",
        "edinet:S100CORRECTION",
    }
    lineage = {
        item.version_id: item for item in updated.evidence_snapshot.source_record_lineage
    }
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

    exported = render_revision_export_markdown(
        RevisionExport(chain=chain, revision=revision)
    )

    assert "## Source Watermarks" in exported
    assert "EDINET: 2026-07-01 to 2026-07-24" in exported
    assert "## Source Record Versions" in exported
    assert "edinet:S100CORRECTION" in exported
    assert "corrected" in exported


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
        claim_coverage = next(
            item for item in draft.coverage.claims if item.object_id == claim.id
        )
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
    ]
    complete_watermarks = [_watermark("EDINET"), _watermark("TDnet")]
    baseline = single_claim(
        assemble_full_revision(
            AnalysisRequest(
                ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)
            ),
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
            AnalysisRequest(
                ticker="6501.T", analysis_date=next_cutoff, analysts=("market",)
            ),
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
    assert updated.outcome is ResearchRevisionOutcome.NO_MATERIAL_CHANGE


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
        claim_coverage = next(
            item for item in draft.coverage.claims if item.object_id == claim.id
        )
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
            AnalysisRequest(
                ticker="6501.T", analysis_date=next_cutoff, analysts=("market",)
            ),
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
    assert updated.outcome.value == "coverage_incomplete"


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


def test_full_update_records_answered_question_as_material_change():
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
        }
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.current_state.questions[0].id == baseline_question.id
    assert updated.delta.questions[0].change.value == "answered"
    assert updated.outcome.value == "material_change"
