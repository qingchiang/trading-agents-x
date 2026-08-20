from __future__ import annotations

import io
import json
import operator
import zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime
from threading import Barrier, Lock
from typing import Annotated, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    ArtifactGenerationMethod,
    CollectionDiagnostic,
    CollectionManifest,
    CollectionManifestEntry,
    CollectionOutcome,
    CoverageRequirement,
    CoverageStatus,
    EvidenceBundle,
    EvidenceItem,
    FullResearchRequiredReason,
    IncrementalCollectionPlan,
    IncrementalCollectionResult,
    IncrementalCollectionSource,
    IncrementalEvidenceCandidate,
    IncrementalSynthesisInput,
    InformationAdvancement,
    PerformanceObservation,
    ResearchArtifactDraft,
    ResearchCoverage,
    ResearchCoverageDomain,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunEvidenceRecord,
    RunRecord,
)
from tradingagents.application.errors import (
    IncrementalRequestConflictError,
    InvalidIncrementalBaselineError,
    NoInformationAdvancementError,
    UnsupportedInstrumentError,
)
from tradingagents.application.incremental_collection import (
    admit_incremental_evidence,
    assess_incremental_collection,
)
from tradingagents.application.llms import RunLLMs
from tradingagents.application.repository import (
    EvidenceConflictError,
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunRepository,
)
from tradingagents.application.runtime import RunCancelled, WorkerShutdown
from tradingagents.application.service import AnalysisService, default_incremental_synthesizer
from tradingagents.dataflows.config import get_config
from tradingagents.graph.research_graph import GraphExecution


def _equity_resolver(ticker: str) -> dict[str, str]:
    return {"symbol": ticker, "quote_type": "EQUITY"}


def test_first_full_run_commits_same_identity_node_and_primary_timeline(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
        now=lambda: datetime(2026, 7, 25, 1, 30, tzinfo=UTC),
    )

    result = service.run(AnalysisRequest(ticker="7203.T", analysis_date=date(2026, 7, 24)))
    timeline = repository.get_timeline("7203.T")
    run = repository.get_run(result.run_id)

    assert result.status is RunStatus.SUCCEEDED
    assert run.research_schema_version == "1"
    assert run.information_cutoff_at == datetime(2026, 7, 24, 14, 59, 59, 999999, tzinfo=UTC)
    assert run.method_snapshot["schema_version"] == "1"
    assert run.method_snapshot["research_schema_version"] == "1"
    assert run.method_snapshot["prompt_versions"]
    assert run.method_snapshot["enabled_roles"] == [
        "market",
        "social",
        "news",
        "fundamentals",
    ]
    assert set(run.method_snapshot["data_routes"]) == {
        "data_vendors",
        "tool_vendors",
        "data_vendors_by_market",
    }
    configured_routes = app_settings.default_run_settings.snapshot()["data_config"]
    assert run.method_snapshot["data_routes"] == {
        key: configured_routes[key]
        for key in ("data_vendors", "tool_vendors", "data_vendors_by_market")
    }
    assert run.method_snapshot["coverage_policy"]["version"] == "1"
    assert run.method_snapshot["thresholds"]["news_article_limit"] == 30
    assert len(run.method_snapshot["configuration_fingerprint"]) == 64
    assert timeline.primary_cycle_id == result.run_id
    assert [(node.id, node.cycle_id, node.is_primary) for node in timeline.nodes] == [
        (result.run_id, result.run_id, True)
    ]


def test_later_full_cycles_require_an_explicit_primary_choice_and_can_be_selected(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )

    first = service.run(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    with pytest.raises(ValueError, match="make_primary"):
        service.run(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    later = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            make_primary=False,
        )
    )
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == sorted(
        (first.run_id, later.run_id)
    )
    assert repository.get_timeline("NVDA").primary_cycle_id == first.run_id

    selected = repository.select_primary_cycle("NVDA", later.run_id)
    repeated = repository.select_primary_cycle("NVDA", later.run_id)

    assert selected.primary_cycle_id == later.run_id
    assert repeated == selected
    assert [node.is_primary for node in selected.nodes] == [
        node.id == later.run_id for node in selected.nodes
    ]


def test_completed_first_full_replays_before_later_full_primary_validation(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")

    first = service.enqueue(request, idempotency_key="first-full-replay")
    claimed = repository.claim_run(first.id, "worker", app_settings.lease_seconds)
    service.execute_claimed(claimed, worker_id="worker")

    replayed = service.enqueue(request, idempotency_key="first-full-replay")

    assert replayed.id == first.id
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(
            AnalysisRequest(ticker="NVDA", analysis_date="2026-07-23"),
            idempotency_key="first-full-replay",
        )


def test_incremental_request_requires_an_explicit_compatible_full_baseline(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))

    with pytest.raises(ValueError, match="full_baseline_run_id"):
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 24), research_kind="incremental")
    with pytest.raises(ValueError, match="must not carry a Full Baseline"):
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="full",
            full_baseline_run_id=baseline.run_id,
        )
    with pytest.raises(InvalidIncrementalBaselineError, match="same Instrument"):
        service.enqueue(
            AnalysisRequest(
                ticker="AAPL",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )


@pytest.mark.parametrize("ticker", ["NVDA", "7203.T", "600000.SS"])
def test_complete_empty_incremental_commits_current_decision_and_timeline_node(
    app_settings,
    repository,
    ticker,
) -> None:
    synthesis_inputs = []
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 20))
    )

    def collect(plan: IncrementalCollectionPlan) -> CollectionManifest:
        return CollectionManifest(
            plan_version=plan.version,
            market=plan.market,
            entries=tuple(
                CollectionManifestEntry(
                    domain=source.domain,
                    source=source.source,
                    provider_identity=source.provider_identity,
                    chain_position=source.chain_position,
                    retrieved_at=plan.window_end if source.configured else None,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start if source.configured else None,
                    scanned_through=plan.window_end if source.configured else None,
                    source_watermark="fixture-watermark" if source.configured else None,
                    outcome=(
                        CollectionOutcome.COMPLETE_EMPTY
                        if source.configured
                        else CollectionOutcome.NOT_APPLICABLE
                    ),
                )
                for source in plan.sources
            ),
        )

    def synthesize(input_):
        synthesis_inputs.append(input_)
        return default_incremental_synthesizer(input_)

    result = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=synthesize,
    ).run(
        AnalysisRequest(
            ticker=ticker,
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    timeline = repository.get_timeline(ticker)
    node = next(item for item in timeline.nodes if item.id == result.run_id)
    assert result.status is RunStatus.SUCCEEDED
    assert node.research_kind == "incremental"
    assert node.full_baseline_run_id == baseline.run_id
    assert node.collection_manifest is not None
    assert node.collection_manifest.entries[0].source_watermark == "fixture-watermark"
    assert node.reassessment is not None
    assert node.outcome_review_status == "omitted"
    assert node.performance.status == "not_yet_observable"
    assert timeline.primary_cycle_id == baseline.run_id
    assert node.is_primary and node.is_cycle_head
    assert len(synthesis_inputs) == 1
    synthesis_input = synthesis_inputs[0]
    assert synthesis_input.incremental_evidence.items == ()
    assert synthesis_input.full_baseline_run_id == baseline.run_id
    assert synthesis_input.method_snapshot["research_schema_version"] == "1"
    assert not hasattr(synthesis_input, "sibling_decision")
    assert result.metrics.llm_calls == 0
    event_types = [event.event_type for event in repository.list_events(result.run_id)]
    assert event_types[-2:] == ["evidence.sealed", "run.succeeded"]


@pytest.mark.parametrize(
    ("ticker", "available_on", "expected_available_at"),
    [
        ("NVDA", date(2026, 7, 21), datetime(2026, 7, 22, 3, 59, 59, 999999, tzinfo=UTC)),
        ("7203.T", date(2026, 7, 21), datetime(2026, 7, 21, 14, 59, 59, 999999, tzinfo=UTC)),
        ("600000.SS", date(2026, 7, 21), datetime(2026, 7, 21, 15, 59, 59, 999999, tzinfo=UTC)),
    ],
)
def test_evidence_bearing_incremental_commits_a_node_local_pit_bundle(
    app_settings,
    repository,
    ticker,
    available_on,
    expected_available_at,
) -> None:
    """A late correction belongs to the child bundle, not its effective period."""
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 20))
    )
    candidate = EvidenceItem.create(
        source="fixture.news",
        evidence_type="correction",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 19),
        content=f"late correction for {ticker}",
    )
    sealed_candidate = EvidenceItem.create(
        source=candidate.source,
        evidence_type=candidate.evidence_type,
        requested_date=candidate.requested_date,
        effective_date=candidate.effective_date,
        available_at=expected_available_at,
        content=candidate.content,
        value=candidate.value,
        measurement_kind=candidate.measurement_kind,
        unit=candidate.unit,
        quality=candidate.quality,
        fallback=candidate.fallback,
        origins=candidate.origins,
        provenance=candidate.provenance,
    )

    def collect(plan: IncrementalCollectionPlan) -> IncrementalCollectionResult:
        entries = []
        for source in plan.sources:
            has_record = source.configured and source.domain == "news"
            entries.append(
                CollectionManifestEntry(
                    domain=source.domain,
                    source=source.source,
                    provider_identity=source.provider_identity,
                    chain_position=source.chain_position,
                    retrieved_at=plan.window_end if source.configured else None,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start if source.configured else None,
                    scanned_through=plan.window_end if source.configured else None,
                    source_watermark="fixture-watermark" if source.configured else None,
                    outcome=(
                        CollectionOutcome.COMPLETE_WITH_RECORDS
                        if has_record
                        else (
                            CollectionOutcome.COMPLETE_EMPTY
                            if source.configured
                            else CollectionOutcome.NOT_APPLICABLE
                        )
                    ),
                    evidence_refs=(candidate.ref,) if has_record else (),
                )
            )
        return IncrementalCollectionResult(
            collection_manifest=CollectionManifest(
                plan_version=plan.version,
                market=plan.market,
                entries=tuple(entries),
            ),
            evidence=(
                IncrementalEvidenceCandidate(
                    evidence=candidate,
                    available_on=available_on,
                ),
            ),
        )

    observed_inputs = []

    def synthesize(input_):
        observed_inputs.append(input_)
        return default_incremental_synthesizer(input_).model_copy(
            update={
                "decision": input_.full_baseline_decision.model_copy(
                    update={
                        "evidence_refs": (
                            *input_.full_baseline_decision.evidence_refs,
                            input_.incremental_evidence.items[0].ref,
                        )
                    }
                )
            }
        )

    result = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=synthesize,
    ).run(
        AnalysisRequest(
            ticker=ticker,
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    committed = repository.get_evidence(result.run_id)
    baseline_evidence = repository.get_evidence(baseline.run_id)
    assert [item.ref for item in committed.items] == [sealed_candidate.ref]
    assert sealed_candidate.ref != candidate.ref
    assert sealed_candidate.ref not in {item.ref for item in baseline_evidence.items}
    assert committed.items[0].effective_date == date(2026, 7, 19)
    assert committed.items[0].available_at == expected_available_at
    assert observed_inputs[0].incremental_evidence == committed
    assert sealed_candidate.ref in result.decision.evidence_refs


def test_incremental_rejects_evidence_without_reliable_availability_before_synthesis(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = EvidenceItem.create(
        source="fixture.news",
        evidence_type="unreliable-publication",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 19),
        content="no reliable publication time",
    )

    def collect(plan: IncrementalCollectionPlan) -> IncrementalCollectionResult:
        manifest = _complete_empty_manifest(plan).model_copy(
            update={
                "entries": (
                    CollectionManifestEntry(
                        domain=plan.sources[0].domain,
                        source=plan.sources[0].source,
                        provider_identity=plan.sources[0].provider_identity,
                        chain_position=plan.sources[0].chain_position,
                        retrieved_at=plan.window_end,
                        planned_from=plan.window_start,
                        planned_through=plan.window_end,
                        scanned_from=plan.window_start,
                        scanned_through=plan.window_end,
                        source_watermark="fixture-watermark",
                        outcome=CollectionOutcome.COMPLETE_WITH_RECORDS,
                        evidence_refs=(candidate.ref,),
                    ),
                    *_complete_empty_manifest(plan).entries[1:],
                )
            }
        )
        return IncrementalCollectionResult(
            collection_manifest=manifest,
            evidence=(IncrementalEvidenceCandidate(evidence=candidate),),
        )

    with pytest.raises(ValueError, match="reliable availability"):
        AnalysisService(
            app_settings,
            repository=repository,
            llm_factory=lambda *_args, **_kwargs: (object(), object()),
            graph_factory=_Graph,
            identity_resolver=lambda symbol, _date: {"company_name": symbol},
            eligibility_resolver=_equity_resolver,
            local_name_resolver=lambda _ticker, _date, _config: None,
            incremental_collector=collect,
            incremental_synthesizer=lambda _input: pytest.fail("must not synthesize"),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert repository.evidence_status(failed[0].id).status == "pending"


def test_incremental_rederives_a_caller_reference_that_collides_with_a_baseline(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    baseline_item = repository.get_evidence(baseline.run_id).items[0]
    conflicting = EvidenceItem.model_construct(
        **{
            **EvidenceItem.create(
                source="fixture.news",
                evidence_type="correction",
                requested_date=date(2026, 7, 24),
                content="different payload under a reused reference",
            ).model_dump(),
            "ref": baseline_item.ref,
        }
    )
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda plan: _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=conflicting, available_on=date(2026, 7, 21)),
        ),
        incremental_synthesizer=default_incremental_synthesizer,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    committed = repository.get_evidence(result.run_id)
    assert committed.items[0].ref != baseline_item.ref
    assert committed.items[0].ref != conflicting.ref
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [
        baseline.run_id,
        result.run_id,
    ]


def test_incremental_rejects_copying_a_baseline_evidence_ref_before_synthesis(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    sealed_baseline_evidence = _seed_baseline_evidence_ownership_collision(
        repository,
        baseline.run_id,
    )
    copied_item = sealed_baseline_evidence.items[0]

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda plan: _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=copied_item),
        ),
        incremental_synthesizer=lambda _input: pytest.fail(
            "baseline-owned Evidence must fail before Incremental synthesis"
        ),
    )

    with pytest.raises(EvidenceConflictError, match="must not copy"):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert failed[0].error_code == "EvidenceConflictError"
    assert repository.get_evidence(baseline.run_id) == sealed_baseline_evidence
    assert repository.evidence_status(failed[0].id).status == "pending"
    assert repository.get_result(failed[0].id).decision is None
    assert repository.get_result(failed[0].id).evidence is None
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [baseline.run_id]
    assert {
        event.event_type for event in repository.list_events(failed[0].id)
    }.isdisjoint({"evidence.sealed", "run.succeeded"})


def test_incremental_repository_rejects_copying_a_baseline_evidence_ref_atomically(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    sealed_baseline_evidence = _seed_baseline_evidence_ownership_collision(
        repository,
        baseline.run_id,
    )
    copied_item = sealed_baseline_evidence.items[0]

    def synthesize(input_):
        synthesis = default_incremental_synthesizer(input_)
        return synthesis.model_copy(
            update={
                "decision": input_.full_baseline_decision.model_copy(
                    update={"evidence_refs": (copied_item.ref,)}
                ),
                "reassessment": synthesis.reassessment.model_copy(
                    update={
                        "entries": (
                            synthesis.reassessment.entries[0].model_copy(
                                update={
                                    "evidence_refs": (copied_item.ref,),
                                    "manifest_entry_refs": (),
                                }
                            ),
                            *synthesis.reassessment.entries[1:],
                        )
                    }
                ),
            }
        )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda plan: _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=copied_item),
        ),
        incremental_synthesizer=synthesize,
    )
    monkeypatch.setattr(
        service,
        "_validate_incremental_bundle_ownership",
        lambda *_args: None,
    )

    with pytest.raises(EvidenceConflictError, match="must not copy"):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert failed[0].error_code == "EvidenceConflictError"
    assert repository.get_evidence(baseline.run_id) == sealed_baseline_evidence
    assert repository.evidence_status(failed[0].id).status == "pending"
    assert repository.get_result(failed[0].id).decision is None
    assert repository.get_result(failed[0].id).evidence is None
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [baseline.run_id]
    assert {
        event.event_type for event in repository.list_events(failed[0].id)
    }.isdisjoint({"evidence.sealed", "run.succeeded"})


def test_incremental_evidence_identity_uses_the_final_available_at_payload() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="japan",
        window_start=datetime(2026, 7, 20, 15, tzinfo=UTC),
        window_end=datetime(2026, 7, 23, 15, tzinfo=UTC),
        required_domains=("news",),
        advisory_domains=(),
        sources=(
            IncrementalCollectionSource(
                domain="news",
                source="fixture.news",
                provider_identity="fixture",
                configured=True,
            ),
        ),
    )
    candidate = EvidenceItem.create(
        source="fixture.news",
        evidence_type="release",
        requested_date=date(2026, 7, 23),
        content="same source payload",
    )

    first = admit_incremental_evidence(
        plan,
        (IncrementalEvidenceCandidate(evidence=candidate, available_on=date(2026, 7, 21)),),
    )[0]
    repeated = admit_incremental_evidence(
        plan,
        (IncrementalEvidenceCandidate(evidence=candidate, available_on=date(2026, 7, 21)),),
    )[0]
    later = admit_incremental_evidence(
        plan,
        (IncrementalEvidenceCandidate(evidence=candidate, available_on=date(2026, 7, 22)),),
    )[0]

    assert first == repeated
    assert first.ref != candidate.ref
    assert later.ref != first.ref


def test_incremental_rejects_one_caller_ref_for_different_final_payloads(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    first = EvidenceItem.create(
        source="fixture.news",
        evidence_type="release",
        requested_date=date(2026, 7, 24),
        content="first payload",
    )
    second = EvidenceItem.model_construct(
        **{
            **EvidenceItem.create(
                source="fixture.news",
                evidence_type="release",
                requested_date=date(2026, 7, 24),
                content="second payload",
            ).model_dump(),
            "ref": first.ref,
        }
    )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda plan: IncrementalCollectionResult(
            collection_manifest=_evidence_bearing_collection(
                plan,
                IncrementalEvidenceCandidate(evidence=first, available_on=date(2026, 7, 21)),
            ).collection_manifest,
            evidence=(
                IncrementalEvidenceCandidate(evidence=first, available_on=date(2026, 7, 21)),
                IncrementalEvidenceCandidate(evidence=second, available_on=date(2026, 7, 22)),
            ),
        ),
        incremental_synthesizer=lambda _input: pytest.fail("must not synthesize"),
    )

    with pytest.raises(ValueError, match="caller reference collides"):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert repository.evidence_status(failed[0].id).status == "pending"


@pytest.mark.parametrize("boundary", ["baseline", "cutoff"])
def test_incremental_evidence_window_is_exact_at_baseline_and_cutoff(
    app_settings,
    repository,
    boundary,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    candidate: EvidenceItem | None = None

    def collect(plan: IncrementalCollectionPlan) -> IncrementalCollectionResult:
        assert candidate is not None
        return _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=candidate),
        )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=default_incremental_synthesizer,
    )
    queued = service.enqueue(request)
    available_at = (
        repository.get_run(baseline.run_id).information_cutoff_at
        if boundary == "baseline"
        else queued.information_cutoff_at
    )
    assert available_at is not None
    candidate = EvidenceItem.create(
        source="fixture.news",
        evidence_type="boundary",
        requested_date=request.analysis_date,
        available_at=available_at,
        content=f"{boundary} boundary",
    )
    if boundary == "baseline":
        with pytest.raises(ValueError, match="baseline-to-cutoff window"):
            service.run(request)
        failed = repository.get_run(queued.id)
        assert failed.status is RunStatus.FAILED
        assert repository.evidence_status(queued.id).status == "pending"
        assert [node.id for node in repository.get_timeline("NVDA").nodes] == [baseline.run_id]
    else:
        result = service.run(request)
        assert result.status is RunStatus.SUCCEEDED
        assert repository.get_evidence(result.run_id).items[0].available_at == available_at


@pytest.mark.parametrize("mutation_phase", ["collection", "synthesis"])
@pytest.mark.parametrize("baseline_mutation", ["trash", "purge"])
def test_incremental_commit_revalidates_a_baseline_mutated_during_execution(
    app_settings,
    repository,
    mutation_phase,
    baseline_mutation,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    second_connection = RunRepository(app_settings)
    candidate = EvidenceItem.create(
        source="fixture.news",
        evidence_type="race-fixture",
        requested_date=date(2026, 7, 24),
        content="baseline mutation race",
    )

    def trash_baseline_from_second_connection() -> None:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(
                _mutate_incremental_baseline,
                second_connection,
                baseline.run_id,
                baseline_mutation,
            ).result()

    def collect(plan: IncrementalCollectionPlan) -> IncrementalCollectionResult:
        if mutation_phase == "collection":
            trash_baseline_from_second_connection()
        return _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=candidate, available_on=date(2026, 7, 21)),
        )

    def synthesize(input_):
        if mutation_phase == "synthesis":
            trash_baseline_from_second_connection()
        return default_incremental_synthesizer(input_)

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=synthesize,
    )
    with pytest.raises(InvalidIncrementalBaselineError):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert repository.evidence_status(failed[0].id).status == "pending"
    assert failed[0].id not in {
        node.id for node in repository.get_timeline("NVDA").nodes
    }
    with repository.sessions() as session:
        assert session.get(DecisionRecord, failed[0].id) is None
        assert session.get(ResearchNodeRecord, failed[0].id) is None
    event_types = {event.event_type for event in repository.list_events(failed[0].id)}
    assert {"evidence.sealed", "run.succeeded"}.isdisjoint(event_types)
    assert "run.failed" in event_types


def test_historical_evidence_backfill_keeps_the_cycle_head_and_excludes_sibling_inputs(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    newer = EvidenceItem.create(
        source="fixture.news",
        evidence_type="newer",
        requested_date=date(2026, 7, 24),
        content="newer sibling evidence",
    )
    first = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda plan: _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=newer, available_on=date(2026, 7, 24)),
        ),
        incremental_synthesizer=default_incremental_synthesizer,
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )
    older = EvidenceItem.create(
        source="fixture.news",
        evidence_type="backfill",
        requested_date=date(2026, 7, 22),
        content="historical sibling evidence",
    )
    synthesis_inputs = []
    backfill = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda plan: _evidence_bearing_collection(
            plan,
            IncrementalEvidenceCandidate(evidence=older, available_on=date(2026, 7, 21)),
        ),
        incremental_synthesizer=lambda input_: (
            synthesis_inputs.append(input_) or default_incremental_synthesizer(input_)
        ),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 22),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    timeline = repository.get_timeline("NVDA")
    by_id = {node.id: node for node in timeline.nodes}
    assert by_id[first.run_id].is_cycle_head
    assert not by_id[backfill.run_id].is_cycle_head
    assert timeline.primary_cycle_id == baseline.run_id
    assert [item.ref for item in synthesis_inputs[0].incremental_evidence.items] == [
        backfill.evidence.items[0].ref
    ]
    assert backfill.evidence.items[0].ref != older.ref
    assert newer.ref not in synthesis_inputs[0].incremental_evidence.model_dump_json()
    assert not hasattr(synthesis_inputs[0], "sibling_decision")


def test_incremental_service_rejects_unclosed_full_research_required_reason(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )

    def collect(plan: IncrementalCollectionPlan) -> CollectionManifest:
        return _complete_empty_manifest(plan)

    def synthesize(input_):
        valid = default_incremental_synthesizer(input_)
        dangling = FullResearchRequiredReason.model_construct(
            code="semantic.unreliable_attribution",
            message="The attribution cannot be relied upon.",
            origin="semantic",
            evidence_refs=("ev_dangling",),
            manifest_entry_refs=(),
        )
        return valid.model_copy(
            update={"full_research_required_reasons": (dangling,)}
        )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=synthesize,
    )

    with pytest.raises(ValueError, match="Full Research Required.*close"):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [
        baseline.run_id
    ]


def test_incremental_repository_rejects_unclosed_reason_atomically(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )

    def synthesize(input_):
        valid = default_incremental_synthesizer(input_)
        sibling_ref = FullResearchRequiredReason.model_construct(
            code="semantic.unreliable_attribution",
            message="A sibling cycle cannot support this warning.",
            origin="semantic",
            evidence_refs=("ev_sibling",),
            manifest_entry_refs=(),
        )
        return valid.model_copy(
            update={"full_research_required_reasons": (sibling_ref,)}
        )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=_complete_empty_manifest,
        incremental_synthesizer=synthesize,
    )
    monkeypatch.setattr(
        service,
        "_validate_full_research_required_reason_closure",
        lambda *_args: None,
    )

    with pytest.raises(EvidenceConflictError, match="outside its closure"):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert repository.evidence_status(failed[0].id).status == "pending"
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [
        baseline.run_id
    ]


@pytest.mark.parametrize(
    ("language", "expected_prompt_label"),
    [
        ("zh-CN", "Simplified Chinese (简体中文, zh-CN)"),
        ("ja", "Japanese (日本語, ja)"),
    ],
)
def test_production_incremental_synthesis_carries_frozen_output_language_through_repair(
    app_settings,
    repository,
    language,
    expected_prompt_label,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    semantic = _PromptSpy("semantic brief")
    serializer = _StructuredPromptSpy()
    manifest_refs: list[str] = []

    def collect(plan: IncrementalCollectionPlan) -> CollectionManifest:
        manifest = _complete_empty_manifest(plan)
        manifest_refs[:] = [
            f"manifest:{entry.domain}:{entry.source}" for entry in manifest.entries
        ]
        return manifest

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: RunLLMs(
            quick=object(),
            deep=semantic,
            quick_serializer=object(),
            deep_serializer=serializer,
        ),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
    )
    serializer_payload = default_incremental_synthesizer(
        _incremental_synthesis_input_for_test(repository, baseline.run_id)
    ).model_dump(mode="json")

    def valid_payload(_prompt: str) -> dict[str, object]:
        payload = deepcopy(serializer_payload)
        for entry in payload["reassessment"]["entries"]:
            entry["manifest_entry_refs"] = [manifest_refs[0]]
        return payload

    serializer.valid = valid_payload

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
            output_language=language,
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(semantic.prompts) == 1
    assert all(expected_prompt_label in prompt for prompt in semantic.prompts)
    assert len(serializer.prompts) == 2
    assert all(expected_prompt_label in prompt for prompt in serializer.prompts)


def test_incremental_atomic_commit_failure_leaves_no_node_or_evidence(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            """CREATE TRIGGER fail_incremental_node BEFORE INSERT ON research_nodes
            WHEN NEW.research_kind = 'incremental'
            BEGIN SELECT RAISE(ABORT, 'injected incremental node failure'); END"""
        )

    def collect(plan: IncrementalCollectionPlan) -> CollectionManifest:
        return CollectionManifest(
            plan_version=plan.version,
            market=plan.market,
            entries=tuple(
                CollectionManifestEntry(
                    domain=source.domain,
                    source=source.source,
                    provider_identity=source.provider_identity,
                    chain_position=source.chain_position,
                    retrieved_at=plan.window_end if source.configured else None,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start if source.configured else None,
                    scanned_through=plan.window_end if source.configured else None,
                    source_watermark="fixture-watermark" if source.configured else None,
                    outcome=CollectionOutcome.COMPLETE_EMPTY
                    if source.configured
                    else CollectionOutcome.NOT_APPLICABLE,
                )
                for source in plan.sources
            ),
        )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=default_incremental_synthesizer,
    )
    with pytest.raises(Exception, match="injected incremental node failure"):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert repository.evidence_status(failed[0].id).status == "pending"
    assert not {
        event.event_type for event in repository.list_events(failed[0].id)
    }.intersection({"evidence.sealed", "run.succeeded"})
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [baseline.run_id]


@pytest.mark.parametrize("ticker", ["NVDA", "7203.T", "600000.SS"])
def test_non_advancing_incremental_run_fails_before_any_semantic_work_or_node_commit(
    app_settings,
    repository,
    ticker,
) -> None:
    llm_calls = 0

    def llm_factory(*_args, **_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return object(), object()

    baseline_service = _service(app_settings, repository)
    baseline = baseline_service.run(AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 20)))
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=llm_factory,
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
    )
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    queued = service.enqueue(request)
    assert queued.request.research_kind == "incremental"
    assert queued.request.full_baseline_run_id == baseline.run_id
    assert queued.method_snapshot["coverage_policy"]["version"] == "1"

    with pytest.raises(NoInformationAdvancementError):
        service.run(request)

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert failed[0].error_code == "NoInformationAdvancementError"
    assert llm_calls == 0
    assert [node.id for node in repository.get_timeline(ticker).nodes] == [baseline.run_id]
    events = repository.list_events(failed[0].id)
    collection_event = next(
        event for event in events if event.event_type == "incremental.collection_completed"
    )
    manifest_entries = collection_event.payload["collection_manifest"]["entries"]
    assert all(entry["source"].endswith(entry["provider_identity"]) for entry in manifest_entries)
    assert {entry["provider_identity"] for entry in manifest_entries}.isdisjoint(
        {"sec_companyfacts", "jquants_statements", "cninfo_disclosures"}
    )
    assert all(entry["retrieved_at"] is None for entry in manifest_entries)
    assert any(event.event_type == "incremental.no_advancement" for event in events)

    replacement = service.enqueue(request)
    assert replacement.id != failed[0].id
    assert replacement.status is RunStatus.QUEUED


@pytest.mark.parametrize(
    ("ticker", "expected_providers"),
    [
        (
            "NVDA",
            {
                "fundamentals": ("yfinance",),
                "market": ("yfinance",),
                "news": ("yfinance",),
            },
        ),
        (
            "7203.T",
            {
                "fundamentals": (
                    "jp_fundamentals",
                    "jp_statements",
                    "jquants",
                    "yfinance",
                ),
                "market": ("jquants", "yfinance"),
                "news": ("jp_news", "yfinance"),
            },
        ),
        (
            "600000.SS",
            {
                "fundamentals": (
                    "cn_fundamentals",
                    "cn_statements",
                    "akshare",
                    "yfinance",
                ),
                "market": ("akshare", "yfinance"),
                "news": ("cn_news", "yfinance"),
            },
        ),
    ],
)
def test_default_incremental_plan_uses_frozen_configured_routes_on_retry(
    app_settings,
    repository,
    ticker,
    expected_providers,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    queued = service.enqueue(request)
    claimed = repository.claim_run(queued.id, "first-worker", app_settings.lease_seconds)
    with pytest.raises(NoInformationAdvancementError):
        service.execute_claimed(claimed, worker_id="first-worker")

    changed_config = deepcopy(app_settings.default_run_settings.data_config)
    changed_config["data_vendors"]["fundamental_data"] = "alpha_vantage"
    changed_settings = app_settings.model_copy(
        update={
            "default_run_settings": app_settings.default_run_settings.model_copy(
                update={"data_config": changed_config}
            )
        }
    )
    retry_service = _service(changed_settings, repository)
    retried = retry_service.retry(queued.id)
    retry_claim = repository.claim_run(
        retried.id,
        "retry-worker",
        changed_settings.lease_seconds,
    )
    with pytest.raises(NoInformationAdvancementError):
        retry_service.execute_claimed(retry_claim, worker_id="retry-worker")

    collection_events = [
        event
        for event in repository.list_events(queued.id)
        if event.event_type == "incremental.collection_completed"
    ]
    assert len(collection_events) == 2
    manifests = [event.payload["collection_manifest"]["entries"] for event in collection_events]
    assert manifests[0] == manifests[1]
    configured_entries = [
        entry for entry in manifests[0] if entry["outcome"] == CollectionOutcome.NOT_QUERIED.value
    ]
    assert {
        domain: tuple(
            entry["provider_identity"] for entry in configured_entries if entry["domain"] == domain
        )
        for domain in expected_providers
    } == expected_providers
    assert {
        domain: tuple(
            entry["chain_position"] for entry in configured_entries if entry["domain"] == domain
        )
        for domain in expected_providers
    } == {domain: tuple(range(len(providers))) for domain, providers in expected_providers.items()}
    assert all(
        entry["source"] == f"{entry['domain']}.{entry['provider_identity']}"
        for entry in configured_entries
    )
    assert all(entry["provider_identity"] != "alpha_vantage" for entry in configured_entries)


@pytest.mark.parametrize(
    ("ticker", "source"),
    [
        ("NVDA", "fundamentals.yfinance"),
        ("7203.T", "fundamentals.jp_fundamentals"),
        ("600000.SS", "fundamentals.cn_fundamentals"),
    ],
)
@pytest.mark.parametrize(
    "outcome",
    [
        CollectionOutcome.COMPLETE_WITH_RECORDS,
        CollectionOutcome.COMPLETE_EMPTY,
        CollectionOutcome.PARTIAL,
        CollectionOutcome.UNAVAILABLE,
        CollectionOutcome.FAILED,
        CollectionOutcome.NOT_QUERIED,
        CollectionOutcome.NOT_APPLICABLE,
    ],
)
def test_incremental_collection_terminal_outcomes_are_structured_and_stop_before_semantic_work(
    app_settings,
    repository,
    ticker,
    source,
    outcome,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 20))
    )
    semantic_calls = 0

    def llm_factory(*_args, **_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        return object(), object()

    def collect(plan: IncrementalCollectionPlan) -> CollectionManifest | IncrementalCollectionResult:
        observed_scan = outcome in {
            CollectionOutcome.COMPLETE_WITH_RECORDS,
            CollectionOutcome.COMPLETE_EMPTY,
            CollectionOutcome.PARTIAL,
        }
        assert any(planned.source == source for planned in plan.sources)
        candidate = EvidenceItem.create(
            source="fixture.collection",
            evidence_type="fixture",
            requested_date=date(2026, 7, 24),
            content=f"{ticker}:{outcome.value}",
        )
        manifest = CollectionManifest(
            plan_version=plan.version,
            market=plan.market,
            entries=tuple(
                CollectionManifestEntry(
                    domain=planned.domain,
                    source=planned.source,
                    provider_identity=planned.provider_identity,
                    chain_position=planned.chain_position,
                    retrieved_at=(
                        plan.window_end
                        if outcome
                        not in {
                            CollectionOutcome.NOT_QUERIED,
                            CollectionOutcome.NOT_APPLICABLE,
                        }
                        else None
                    ),
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start if observed_scan else None,
                    scanned_through=(
                        plan.window_end
                        if outcome is not CollectionOutcome.PARTIAL
                        else datetime(2026, 7, 22, tzinfo=UTC)
                    )
                    if observed_scan
                    else None,
                    source_watermark=(
                        "fixture-watermark" if outcome is CollectionOutcome.COMPLETE_EMPTY else None
                    ),
                    outcome=outcome,
                        evidence_refs=(
                            (candidate.ref,)
                            if outcome is CollectionOutcome.COMPLETE_WITH_RECORDS
                        else ()
                    ),
                    diagnostic=(
                        CollectionDiagnostic(
                            code="fixture_source_unavailable",
                        )
                        if outcome in {CollectionOutcome.UNAVAILABLE, CollectionOutcome.FAILED}
                        else None
                    ),
                )
                for planned in plan.sources
            ),
        )
        if outcome is CollectionOutcome.COMPLETE_WITH_RECORDS:
            return IncrementalCollectionResult(
                collection_manifest=manifest,
                evidence=(
                    IncrementalEvidenceCandidate(
                        evidence=candidate,
                        available_on=date(2026, 7, 21),
                    ),
                ),
            )
        return manifest

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=llm_factory,
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=default_incremental_synthesizer,
    )
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    if outcome in {CollectionOutcome.COMPLETE_EMPTY, CollectionOutcome.COMPLETE_WITH_RECORDS}:
        result = service.run(request)
        assert result.status is RunStatus.SUCCEEDED
        assert any(node.id == result.run_id for node in repository.get_timeline(ticker).nodes)
        return
    with pytest.raises(NoInformationAdvancementError):
        service.run(request)

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert semantic_calls == 0
    assert [node.id for node in repository.get_timeline(ticker).nodes] == [baseline.run_id]
    collection_event = next(
        event
        for event in repository.list_events(failed[0].id)
        if event.event_type == "incremental.collection_completed"
    )
    assert collection_event.payload["collection_manifest"]["market"] in {
        "united_states",
        "japan",
        "mainland_china",
    }
    assert collection_event.payload["research_coverage"]["domains"][0]["status"] in {
        "complete",
        "limited",
        "missing",
        "not_applicable",
    }
    assert collection_event.payload["information_advancement"]["advanced"] is (
        outcome in {CollectionOutcome.COMPLETE_EMPTY, CollectionOutcome.COMPLETE_WITH_RECORDS}
    )
    assert collection_event.payload["diagnostics"] == (
        [{"code": "fixture_source_unavailable"}]
        if outcome in {CollectionOutcome.UNAVAILABLE, CollectionOutcome.FAILED}
        else []
    )


def test_incremental_collection_assessment_distinguishes_each_terminal_outcome() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="united_states",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("fundamentals", "market", "news"),
        advisory_domains=("social",),
        sources=(
            IncrementalCollectionSource(
                domain="fundamentals",
                source="sec_companyfacts",
                provider_identity="sec_companyfacts",
                configured=True,
            ),
            IncrementalCollectionSource(
                domain="market",
                source="market_series",
                provider_identity="market_series",
                configured=True,
            ),
            IncrementalCollectionSource(
                domain="news",
                source="ticker_news",
                provider_identity="ticker_news",
                configured=True,
            ),
            IncrementalCollectionSource(
                domain="social",
                source="social_sentiment",
                provider_identity="social_sentiment",
                configured=True,
            ),
            IncrementalCollectionSource(
                domain="social",
                source="social_fallback",
                provider_identity="social_fallback",
                chain_position=1,
                configured=True,
            ),
        ),
    )
    result = assess_incremental_collection(
        plan,
        CollectionManifest(
            plan_version="1",
            market="united_states",
            entries=(
                CollectionManifestEntry(
                    domain="fundamentals",
                    source="sec_companyfacts",
                    provider_identity="sec_companyfacts",
                    retrieved_at=plan.window_end,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start,
                    scanned_through=plan.window_end,
                    source_watermark="fixture-watermark",
                    outcome=CollectionOutcome.COMPLETE_EMPTY,
                ),
                CollectionManifestEntry(
                    domain="market",
                    source="market_series",
                    provider_identity="market_series",
                    retrieved_at=plan.window_end,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start,
                    scanned_through=plan.window_end,
                    outcome=CollectionOutcome.COMPLETE_WITH_RECORDS,
                    evidence_refs=("ev_0123456789ab",),
                ),
                CollectionManifestEntry(
                    domain="news",
                    source="ticker_news",
                    provider_identity="ticker_news",
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    outcome=CollectionOutcome.NOT_APPLICABLE,
                ),
                CollectionManifestEntry(
                    domain="social",
                    source="social_sentiment",
                    provider_identity="social_sentiment",
                    retrieved_at=plan.window_end,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start,
                    scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
                    outcome=CollectionOutcome.PARTIAL,
                ),
                CollectionManifestEntry(
                    domain="social",
                    source="social_fallback",
                    provider_identity="social_fallback",
                    chain_position=1,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    outcome=CollectionOutcome.NOT_QUERIED,
                ),
            ),
        ),
    )

    assert result.research_coverage == ResearchCoverage.model_validate(
        {
            "policy_version": "1",
            "domains": [
                {"domain": "fundamentals", "requirement": "required", "status": "complete"},
                {"domain": "market", "requirement": "required", "status": "complete"},
                {"domain": "news", "requirement": "required", "status": "not_applicable"},
                {"domain": "social", "requirement": "advisory", "status": "limited"},
            ],
        }
    )
    assert result.information_advancement == InformationAdvancement(
        advanced=True,
        reasons=("complete_empty_scan", "admissible_evidence"),
    )


def test_partial_collection_with_admitted_evidence_advances_information() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="united_states",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("fundamentals", "market", "news"),
        advisory_domains=("social",),
        sources=(
            IncrementalCollectionSource(
                domain="news",
                source="news.yfinance",
                provider_identity="yfinance",
                configured=True,
            ),
        ),
    )
    manifest = CollectionManifest(
        plan_version=plan.version,
        market=plan.market,
        entries=(
            CollectionManifestEntry(
                domain="news",
                source="news.yfinance",
                provider_identity="yfinance",
                retrieved_at=plan.window_end,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                scanned_from=plan.window_start,
                scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
                outcome=CollectionOutcome.PARTIAL,
                evidence_refs=("ev_0123456789ab",),
            ),
        ),
    )

    result = assess_incremental_collection(plan, manifest)

    assert result.information_advancement == InformationAdvancement(
        advanced=True,
        reasons=("admissible_evidence",),
    )


@pytest.mark.parametrize(
    ("scanned_from", "scanned_through"),
    [
        (None, datetime(2026, 7, 22, tzinfo=UTC)),
        (datetime(2026, 7, 20, tzinfo=UTC), None),
        (datetime(2026, 7, 22, tzinfo=UTC), datetime(2026, 7, 22, tzinfo=UTC)),
        (datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 25, tzinfo=UTC)),
    ],
)
def test_partial_collection_requires_a_nonempty_scanned_interval_within_plan(
    scanned_from: datetime | None,
    scanned_through: datetime | None,
) -> None:
    with pytest.raises(ValidationError, match="scanned interval"):
        CollectionManifestEntry(
            domain="news",
            source="ticker_news",
            provider_identity="provider_news",
            retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
            planned_from=datetime(2026, 7, 20, tzinfo=UTC),
            planned_through=datetime(2026, 7, 24, tzinfo=UTC),
            scanned_from=scanned_from,
            scanned_through=scanned_through,
            outcome=CollectionOutcome.PARTIAL,
        )


def test_incremental_collection_rejects_a_manifest_source_absent_from_the_plan() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="united_states",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("fundamentals", "market", "news"),
        advisory_domains=("social",),
        sources=(
            IncrementalCollectionSource(
                domain="fundamentals",
                source="fundamentals.yfinance",
                provider_identity="yfinance",
                configured=True,
            ),
        ),
    )
    manifest = CollectionManifest(
        plan_version=plan.version,
        market=plan.market,
        entries=(
            CollectionManifestEntry(
                domain="fundamentals",
                source="fundamentals.alpha_vantage",
                provider_identity="alpha_vantage",
                retrieved_at=plan.window_end,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                scanned_from=plan.window_start,
                scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
                outcome=CollectionOutcome.PARTIAL,
            ),
        ),
    )

    with pytest.raises(ValueError, match="unconfigured source"):
        assess_incremental_collection(plan, manifest)


@pytest.mark.parametrize("observation", ["missing", "duplicate", "wrong_provider"])
def test_incremental_collection_requires_one_exact_observation_per_planned_source(
    observation: str,
) -> None:
    """Coverage never assesses a partial or substituted deterministic plan."""
    plan = IncrementalCollectionPlan(
        version="1",
        market="japan",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("fundamentals", "market", "news"),
        advisory_domains=("social",),
        sources=(
            IncrementalCollectionSource(
                domain="news",
                source="news.tdnet",
                provider_identity="tdnet",
                configured=True,
            ),
            IncrementalCollectionSource(
                domain="news",
                source="news.google",
                provider_identity="google_news",
                chain_position=1,
                configured=True,
            ),
        ),
    )
    entries = [
        CollectionManifestEntry(
            domain="news",
            source="news.tdnet",
            provider_identity="tdnet",
            planned_from=plan.window_start,
            planned_through=plan.window_end,
            outcome=CollectionOutcome.NOT_QUERIED,
        ),
        CollectionManifestEntry(
            domain="news",
            source="news.google",
            provider_identity="google_news",
            chain_position=1,
            planned_from=plan.window_start,
            planned_through=plan.window_end,
            outcome=CollectionOutcome.NOT_QUERIED,
        ),
    ]
    if observation == "missing":
        entries.pop()
    elif observation == "duplicate":
        entries[1] = entries[0]
    else:
        entries[1] = entries[1].model_copy(update={"provider_identity": "yfinance"})

    manifest = CollectionManifest(
        plan_version=plan.version,
        market=plan.market,
        entries=tuple(entries),
    )

    with pytest.raises(ValueError, match="exactly match"):
        assess_incremental_collection(plan, manifest)


def test_incremental_collection_rejects_reordered_fallback_observations() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="japan",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("news",),
        advisory_domains=(),
        sources=(
            IncrementalCollectionSource(
                domain="news",
                source="news.tdnet",
                provider_identity="tdnet",
                configured=True,
            ),
            IncrementalCollectionSource(
                domain="news",
                source="news.google",
                provider_identity="google_news",
                chain_position=1,
                configured=True,
            ),
        ),
    )
    entries = tuple(
        CollectionManifestEntry(
            domain=source.domain,
            source=source.source,
            provider_identity=source.provider_identity,
            planned_from=plan.window_start,
            planned_through=plan.window_end,
            outcome=CollectionOutcome.NOT_QUERIED,
        )
        for source in reversed(plan.sources)
    )

    with pytest.raises(ValueError, match="ordered"):
        assess_incremental_collection(
            plan,
            CollectionManifest(
                plan_version=plan.version,
                market=plan.market,
                entries=entries,
            ),
        )


def test_incremental_collection_rejects_a_manifest_interval_that_differs_from_frozen_plan() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="united_states",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("news",),
        advisory_domains=(),
        sources=(
            IncrementalCollectionSource(
                domain="news",
                source="news.yfinance",
                provider_identity="yfinance",
                configured=True,
            ),
        ),
    )
    entry = CollectionManifestEntry(
        domain="news",
        source="news.yfinance",
        provider_identity="yfinance",
        retrieved_at=plan.window_end,
        planned_from=datetime(2026, 7, 21, tzinfo=UTC),
        planned_through=plan.window_end,
        scanned_from=datetime(2026, 7, 21, tzinfo=UTC),
        scanned_through=plan.window_end,
        source_watermark="fixture-watermark",
        outcome=CollectionOutcome.COMPLETE_EMPTY,
    )

    with pytest.raises(ValueError, match="frozen plan interval"):
        assess_incremental_collection(
            plan,
            CollectionManifest(
                plan_version=plan.version,
                market=plan.market,
                entries=(entry,),
            ),
        )


def test_incremental_preflight_keeps_provider_retrieval_and_newly_reviewable_input() -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="united_states",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("fundamentals", "market", "news"),
        advisory_domains=("social",),
        sources=(
            IncrementalCollectionSource(
                domain="fundamentals",
                source="sec_companyfacts",
                provider_identity="sec_edgar",
                configured=True,
            ),
        ),
    )
    manifest = CollectionManifest(
        plan_version=plan.version,
        market=plan.market,
        entries=(
            CollectionManifestEntry(
                domain="fundamentals",
                source="sec_companyfacts",
                provider_identity="sec_edgar",
                retrieved_at=plan.window_end,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                scanned_from=plan.window_start,
                scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
                outcome=CollectionOutcome.PARTIAL,
            ),
        ),
        newly_reviewable_baseline_component_ids=("decision.thesis",),
    )

    result = assess_incremental_collection(plan, manifest)

    assert result.collection_manifest.model_dump(mode="json")["entries"] == [
        {
            "domain": "fundamentals",
            "source": "sec_companyfacts",
            "provider_identity": "sec_edgar",
            "chain_position": 0,
            "retrieved_at": "2026-07-24T00:00:00Z",
            "planned_from": "2026-07-20T00:00:00Z",
            "planned_through": "2026-07-24T00:00:00Z",
            "scanned_from": "2026-07-20T00:00:00Z",
            "scanned_through": "2026-07-22T00:00:00Z",
            "source_watermark": None,
            "outcome": "partial",
            "evidence_refs": [],
            "diagnostic": None,
        }
    ]
    assert result.information_advancement == InformationAdvancement(
        advanced=True,
        reasons=("newly_reviewable_baseline_component",),
        newly_reviewable_baseline_component_ids=("decision.thesis",),
    )


def test_newly_reviewable_baseline_component_commits_without_sibling_inputs(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )

    def collect(plan: IncrementalCollectionPlan) -> CollectionManifest:
        return CollectionManifest(
            plan_version=plan.version,
            market=plan.market,
            entries=tuple(
                CollectionManifestEntry(
                    domain=source.domain,
                    source=source.source,
                    provider_identity=source.provider_identity,
                    chain_position=source.chain_position,
                    retrieved_at=plan.window_end,
                    planned_from=plan.window_start,
                    planned_through=plan.window_end,
                    scanned_from=plan.window_start,
                    scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
                    outcome=CollectionOutcome.PARTIAL,
                )
                for source in plan.sources
            ),
            newly_reviewable_baseline_component_ids=("decision.thesis",),
        )

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
        incremental_synthesizer=default_incremental_synthesizer,
    )
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    result = service.run(request)

    events = repository.list_events(result.run_id)
    collection_event = next(
        event for event in events if event.event_type == "incremental.collection_completed"
    )
    assert collection_event.payload["information_advancement"] == {
        "advanced": True,
        "reasons": ["newly_reviewable_baseline_component"],
        "newly_reviewable_baseline_component_ids": ["decision.thesis"],
    }
    assert not any(event.event_type == "incremental.no_advancement" for event in events)
    assert [node.id for node in repository.get_timeline("NVDA").nodes] == [
        baseline.run_id,
        result.run_id,
    ]


def test_collection_manifest_requires_retrieval_time_for_a_queried_source() -> None:
    with pytest.raises(ValidationError, match="retrieval time"):
        CollectionManifestEntry(
            domain="news",
            source="ticker_news",
            provider_identity="provider_news",
            planned_from=datetime(2026, 7, 20, tzinfo=UTC),
            planned_through=datetime(2026, 7, 24, tzinfo=UTC),
            scanned_from=datetime(2026, 7, 20, tzinfo=UTC),
            scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
            outcome=CollectionOutcome.PARTIAL,
        )


@pytest.mark.parametrize(
    "outcome",
    [CollectionOutcome.UNAVAILABLE, CollectionOutcome.FAILED],
)
def test_collection_manifest_requires_a_safe_diagnostic_for_terminal_failure(
    outcome: CollectionOutcome,
) -> None:
    with pytest.raises(ValidationError, match="sanitized diagnostic"):
        CollectionManifestEntry(
            domain="news",
            source="ticker_news",
            provider_identity="provider_news",
            retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
            planned_from=datetime(2026, 7, 20, tzinfo=UTC),
            planned_through=datetime(2026, 7, 24, tzinfo=UTC),
            outcome=outcome,
        )


@pytest.mark.parametrize(
    "outcome",
    [CollectionOutcome.NOT_QUERIED, CollectionOutcome.NOT_APPLICABLE],
)
def test_collection_manifest_rejects_diagnostics_for_unqueried_outcomes(
    outcome: CollectionOutcome,
) -> None:
    with pytest.raises(ValidationError, match="diagnostic"):
        CollectionManifestEntry(
            domain="news",
            source="ticker_news",
            provider_identity="provider_news",
            planned_from=datetime(2026, 7, 20, tzinfo=UTC),
            planned_through=datetime(2026, 7, 24, tzinfo=UTC),
            outcome=outcome,
            diagnostic=CollectionDiagnostic(code="raw_exception_leaked"),
        )


@pytest.mark.parametrize(
    "outcome",
    [
        CollectionOutcome.COMPLETE_EMPTY,
        CollectionOutcome.UNAVAILABLE,
        CollectionOutcome.FAILED,
        CollectionOutcome.NOT_QUERIED,
        CollectionOutcome.NOT_APPLICABLE,
    ],
)
def test_collection_manifest_forbids_evidence_for_non_evidence_terminal_outcomes(
    outcome: CollectionOutcome,
) -> None:
    fields = {
        "domain": "news",
        "source": "ticker_news",
        "provider_identity": "provider_news",
        "planned_from": datetime(2026, 7, 20, tzinfo=UTC),
        "planned_through": datetime(2026, 7, 24, tzinfo=UTC),
        "evidence_refs": ("ev_0123456789ab",),
    }
    if outcome is CollectionOutcome.COMPLETE_EMPTY:
        fields.update(
            retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
            scanned_from=datetime(2026, 7, 20, tzinfo=UTC),
            scanned_through=datetime(2026, 7, 24, tzinfo=UTC),
            source_watermark="fixture-watermark",
        )
    elif outcome not in {
        CollectionOutcome.NOT_QUERIED,
        CollectionOutcome.NOT_APPLICABLE,
    }:
        fields["retrieved_at"] = datetime(2026, 7, 24, tzinfo=UTC)

    with pytest.raises(ValidationError, match="evidence"):
        CollectionManifestEntry(outcome=outcome, **fields)


@pytest.mark.parametrize(
    ("outcome", "evidence_refs", "expected_reason"),
    [
        (CollectionOutcome.COMPLETE_WITH_RECORDS, ("ev_0123456789ab",), "admissible_evidence"),
        (CollectionOutcome.COMPLETE_EMPTY, (), "complete_empty_scan"),
        (CollectionOutcome.PARTIAL, ("ev_0123456789ab",), "admissible_evidence"),
        (CollectionOutcome.PARTIAL, (), None),
        (CollectionOutcome.UNAVAILABLE, (), None),
        (CollectionOutcome.FAILED, (), None),
        (CollectionOutcome.NOT_QUERIED, (), None),
        (CollectionOutcome.NOT_APPLICABLE, (), None),
    ],
)
def test_incremental_advancement_accepts_only_complete_or_evidence_bearing_outcomes(
    outcome: CollectionOutcome,
    evidence_refs: tuple[str, ...],
    expected_reason: str | None,
) -> None:
    plan = IncrementalCollectionPlan(
        version="1",
        market="united_states",
        window_start=datetime(2026, 7, 20, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, tzinfo=UTC),
        required_domains=("news",),
        advisory_domains=(),
        sources=(
            IncrementalCollectionSource(
                domain="news",
                source="news.yfinance",
                provider_identity="yfinance",
                configured=True,
            ),
        ),
    )
    entry = {
        "domain": "news",
        "source": "news.yfinance",
        "provider_identity": "yfinance",
        "planned_from": plan.window_start,
        "planned_through": plan.window_end,
        "outcome": outcome,
        "evidence_refs": evidence_refs,
    }
    if outcome in {
        CollectionOutcome.COMPLETE_WITH_RECORDS,
        CollectionOutcome.COMPLETE_EMPTY,
    }:
        entry.update(
            retrieved_at=plan.window_end,
            scanned_from=plan.window_start,
            scanned_through=plan.window_end,
            source_watermark="fixture-watermark",
        )
    elif outcome not in {
        CollectionOutcome.NOT_QUERIED,
        CollectionOutcome.NOT_APPLICABLE,
    }:
        entry["retrieved_at"] = plan.window_end
    if outcome is CollectionOutcome.PARTIAL:
        entry.update(
            scanned_from=plan.window_start,
            scanned_through=datetime(2026, 7, 22, tzinfo=UTC),
        )
    if outcome in {CollectionOutcome.UNAVAILABLE, CollectionOutcome.FAILED}:
        entry["diagnostic"] = CollectionDiagnostic(code="fixture_failure")

    result = assess_incremental_collection(
        plan,
        CollectionManifest(
            plan_version=plan.version,
            market=plan.market,
            entries=(CollectionManifestEntry(**entry),),
        ),
    )

    assert result.information_advancement.advanced is (expected_reason is not None)
    assert result.information_advancement.reasons == (
        (expected_reason,) if expected_reason is not None else ()
    )


def test_incremental_slot_replays_identical_active_request_and_rejects_conflict(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    first = service.enqueue(request)
    assert service.enqueue(request).id == first.id

    with pytest.raises(IncrementalRequestConflictError):
        service.enqueue(request.model_copy(update={"analysts": ("market",)}))


def test_two_connections_return_one_incremental_slot_for_identical_requests(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        runs = list(executor.map(lambda _unused: service.enqueue(request), range(2)))

    assert {run.id for run in runs} == {runs[0].id}


@pytest.mark.parametrize(
    ("terminal_state", "expected_status", "trashed"),
    [
        ("failed", RunStatus.FAILED, False),
        ("cancelled", RunStatus.CANCELLED, False),
        ("trashed", RunStatus.CANCELLED, True),
    ],
)
def test_incremental_retry_keeps_inactive_history_when_a_conflicting_slot_is_active(
    app_settings,
    repository,
    terminal_state: str,
    expected_status: RunStatus,
    trashed: bool,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    failed_request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    inactive = service.enqueue(failed_request)
    if terminal_state == "failed":
        repository.claim_run(inactive.id, "fixture", app_settings.lease_seconds)
        repository.fail(inactive.id, RuntimeError("fixture failure"))
    else:
        service.cancel(inactive.id)
        if terminal_state == "trashed":
            repository.trash_runs((inactive.id,))
    active = service.enqueue(failed_request.model_copy(update={"analysts": ("market",)}))

    expected_error = (
        IncrementalRequestConflictError if terminal_state == "failed" else InvalidRunTransitionError
    )
    with pytest.raises(expected_error):
        service.retry(inactive.id)

    unchanged = repository.get_run(inactive.id)
    assert unchanged.status is expected_status
    assert unchanged.attempt == 1
    assert (unchanged.trashed_at is not None) is trashed
    assert repository.get_run(active.id).status is RunStatus.QUEUED


def test_incremental_retry_replays_an_identical_active_slot_only_from_failed_history(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    inactive = service.enqueue(request)
    repository.claim_run(inactive.id, "fixture", app_settings.lease_seconds)
    repository.fail(inactive.id, RuntimeError("fixture failure"))
    active = service.enqueue(request)

    replayed = service.retry(inactive.id)

    assert replayed.id == active.id
    unchanged = repository.get_run(inactive.id)
    assert unchanged.status is RunStatus.FAILED
    assert unchanged.attempt == 1
    assert unchanged.trashed_at is None
    assert not any(
        event.event_type == "run.retry_queued" for event in repository.list_events(inactive.id)
    )


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_incremental_retry_rejects_an_active_target_before_slot_replay(
    app_settings,
    repository,
    status: RunStatus,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    target = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )
    if status is RunStatus.RUNNING:
        repository.claim_run(target.id, "fixture", app_settings.lease_seconds)

    events_before = repository.list_events(target.id)
    with pytest.raises(InvalidRunTransitionError, match="only failed runs"):
        service.retry(target.id)

    unchanged = repository.get_run(target.id)
    assert unchanged.status is status
    assert unchanged.attempt == 1
    assert repository.list_events(target.id) == events_before


def test_incremental_retry_maps_a_sqlite_slot_integrity_error_to_typed_conflict(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    failed = service.enqueue(request)
    repository.claim_run(failed.id, "fixture", app_settings.lease_seconds)
    repository.fail(failed.id, RuntimeError("fixture failure"))
    service.enqueue(request.model_copy(update={"analysts": ("market",)}))
    active_slot = repository._active_incremental_slot
    reads = 0

    def stale_first_slot_read(*args):
        nonlocal reads
        reads += 1
        if reads == 1:
            return None
        return active_slot(*args)

    monkeypatch.setattr(repository, "_active_incremental_slot", stale_first_slot_read)

    with pytest.raises(IncrementalRequestConflictError):
        service.retry(failed.id)

    assert reads == 2
    unchanged = repository.get_run(failed.id)
    assert unchanged.status is RunStatus.FAILED
    assert unchanged.attempt == 1


def test_two_sqlite_connections_make_one_incremental_retry_slot_winner(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    failed = service.enqueue(request)
    repository.claim_run(failed.id, "fixture", app_settings.lease_seconds)
    repository.fail(failed.id, RuntimeError("fixture failure"))
    conflicting_request = request.model_copy(update={"analysts": ("market",)})
    barrier = Barrier(2)

    def attempt(operation):
        barrier.wait(timeout=10)
        try:
            return operation()
        except Exception as exc:  # Both service calls share the public error seam.
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                attempt,
                (
                    lambda: service.retry(failed.id),
                    lambda: service.enqueue(conflicting_request),
                ),
            )
        )

    successful = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    conflicts = [
        outcome for outcome in outcomes if isinstance(outcome, IncrementalRequestConflictError)
    ]
    assert len(successful) == 1
    assert len(conflicts) == 1
    assert repository.get_run(successful[0].id).status is RunStatus.QUEUED
    retained = repository.get_run(failed.id)
    assert retained.status in {RunStatus.FAILED, RunStatus.QUEUED}
    assert retained.attempt in {1, 2}


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("backend_url", "https://changed-gateway.example.invalid/v1"),
        ("temperature", 0.7),
        ("llm_max_retries", 5),
    ],
)
def test_method_snapshot_records_resolved_llm_settings_in_its_fingerprint(
    app_settings,
    repository,
    field,
    changed_value,
) -> None:
    """Queued Runs retain non-secret LLM behavior that can change a method."""
    base_run_settings = app_settings.default_run_settings.model_copy(
        update={
            "backend_url": "https://gateway.example.invalid/v1",
            "temperature": 0.2,
            "llm_max_retries": 3,
            "data_config": {
                **app_settings.default_run_settings.data_config,
                "provider_api_key": "method-snapshot-test-secret",
            },
        }
    )
    base_settings = app_settings.model_copy(update={"default_run_settings": base_run_settings})
    request = AnalysisRequest(ticker="7203.T", analysis_date=date(2026, 7, 24))

    base_run = AnalysisService(
        base_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    ).enqueue(request, idempotency_key=f"method-snapshot-base-{field}")
    base_snapshot = repository.get_run(base_run.id).method_snapshot

    assert base_snapshot["backend_url"] == "https://gateway.example.invalid/v1"
    assert base_snapshot["temperature"] == 0.2
    assert base_snapshot["llm_max_retries"] == 3
    assert "method-snapshot-test-secret" not in json.dumps(base_snapshot)

    changed_run_settings = base_run_settings.model_copy(update={field: changed_value})
    changed_settings = app_settings.model_copy(
        update={"default_run_settings": changed_run_settings}
    )
    changed_run = AnalysisService(
        changed_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    ).enqueue(request, idempotency_key=f"method-snapshot-changed-{field}")
    changed_snapshot = repository.get_run(changed_run.id).method_snapshot

    assert changed_snapshot[field] == changed_value
    assert (
        changed_snapshot["configuration_fingerprint"] != base_snapshot["configuration_fingerprint"]
    )


def test_current_market_day_freezes_current_instant_and_future_rejects_without_run(
    app_settings,
    repository,
) -> None:
    now = datetime(2026, 7, 24, 15, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
        now=lambda: now,
    )

    run = service.enqueue(AnalysisRequest(ticker="7203.T", analysis_date=now.date()))
    assert run.information_cutoff_at == now.astimezone(UTC)

    with pytest.raises(ValueError, match="future analysis cutoff"):
        service.enqueue(AnalysisRequest(ticker="7203.T", analysis_date=date(2026, 7, 25)))
    assert repository.list_runs().total == 1


@pytest.mark.parametrize(
    "trigger",
    [
        "decision_insert",
        "node_insert",
        "primary_insert",
        "run_success",
        "attempt_success",
    ],
)
def test_atomic_research_commit_rolls_back_every_persisted_boundary(
    app_settings,
    repository,
    trigger,
) -> None:
    """SQLite failures leave the lifecycle in failed History, never partial Timeline."""
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    trigger_sql = {
        "decision_insert": """
            CREATE TRIGGER fail_decision_insert BEFORE INSERT ON decisions
            BEGIN SELECT RAISE(ABORT, 'injected decision failure'); END
        """,
        "node_insert": """
            CREATE TRIGGER fail_node_insert BEFORE INSERT ON research_nodes
            BEGIN SELECT RAISE(ABORT, 'injected node failure'); END
        """,
        "primary_insert": """
            CREATE TRIGGER fail_primary_insert BEFORE INSERT ON primary_research_cycles
            BEGIN SELECT RAISE(ABORT, 'injected primary failure'); END
        """,
        "run_success": """
            CREATE TRIGGER fail_run_success BEFORE UPDATE OF status ON runs
            WHEN NEW.status = 'succeeded'
            BEGIN SELECT RAISE(ABORT, 'injected run success failure'); END
        """,
        "attempt_success": """
            CREATE TRIGGER fail_attempt_success BEFORE UPDATE OF status ON run_attempts
            WHEN NEW.status = 'succeeded'
            BEGIN SELECT RAISE(ABORT, 'injected attempt success failure'); END
        """,
    }[trigger]
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(trigger_sql)

    with pytest.raises(Exception, match="injected"):
        service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 24)))

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    with repository.sessions() as session:
        assert session.query(DecisionRecord).count() == 0
        assert session.query(ResearchNodeRecord).count() == 0
        assert session.query(PrimaryResearchCycleRecord).count() == 0
    assert repository.get_timeline("NVDA").nodes == ()


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("NVDA", datetime(2026, 7, 25, 3, 59, 59, 999999, tzinfo=UTC)),
        ("7203.T", datetime(2026, 7, 24, 14, 59, 59, 999999, tzinfo=UTC)),
        ("600000.SS", datetime(2026, 7, 24, 15, 59, 59, 999999, tzinfo=UTC)),
    ],
)
def test_historical_cutoffs_use_each_listed_instrument_market_day_end(
    app_settings,
    repository,
    ticker,
    expected,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
        now=lambda: datetime(2026, 7, 26, tzinfo=UTC),
    )

    run = service.enqueue(AnalysisRequest(ticker=ticker, analysis_date="2026-07-24"))

    assert run.information_cutoff_at == expected


def _execution(ticker: str) -> GraphExecution:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture evidence",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture evidence.",
    )
    bundle = EvidenceBundle(
        instrument=ticker,
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(
        executive_summary="Fixture summary.",
        confidence=0.8,
        evidence_ref=item.ref,
        narrative="Fixture report.",
    )
    decision = research_decision(
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(item.ref,),
    )
    return GraphExecution(
        state={},
        evidence=bundle,
        reports={"market": report},
        decision=decision,
    )


class _Graph:
    barrier: Barrier | None = None
    observed: list[tuple[str, str, str]] = []
    lock = Lock()
    error: Exception | None = None

    def __init__(self, **_kwargs):
        pass

    def execute(self, context, *, on_event, **_kwargs):
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        with self.lock:
            self.observed.append(
                (
                    context.request.ticker,
                    context.settings.llm_provider,
                    get_config()["llm_provider"],
                )
            )
        on_event(
            {
                "event_type": "node.completed",
                "node": "fixture",
                "payload": {"api_key": "must-not-persist"},
            }
        )
        if self.error is not None:
            raise self.error
        return _execution(context.request.ticker)


class _ArtifactGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        execution = _execution(context.request.ticker)
        context.artifact_writer(
            ResearchArtifactDraft(
                node="analyst.market",
                stage="analyst",
                role="market",
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=execution.reports["market"],
            )
        )
        return execution


class _DecisionlessGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        execution = _execution(context.request.ticker)
        return GraphExecution(
            state=execution.state,
            evidence=execution.evidence,
            reports=execution.reports,
            decision=None,
        )


def test_failed_atomic_full_commit_keeps_execution_history_without_node_or_decision(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_DecisionlessGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )

    with pytest.raises(ValueError, match="complete Research Decision"):
        service.run(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    failed = repository.list_runs(status=RunStatus.FAILED).items[0]
    assert repository.get_timeline("NVDA").nodes == ()
    assert repository.get_result(failed.id).decision is None


def test_queued_legacy_run_fails_execution_boundary_without_a_node(
    app_settings,
    repository,
) -> None:
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    run, _ = repository.create_run(request, {"fixture": True})
    claimed = repository.claim_run(run.id, "worker", 30)
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    )

    with pytest.raises(ValueError, match="legacy runs"):
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_run(run.id).status is RunStatus.FAILED
    assert repository.get_timeline("NVDA").nodes == ()


class _MetricFailureGraph:
    def __init__(self, *, metrics, **_kwargs):
        self.metrics = metrics

    def execute(self, _context, **_kwargs):
        run_id = uuid4()
        self.metrics.on_llm_start(
            {},
            ["fixture"],
            run_id=run_id,
            metadata={"research_node": "analyst.market.serialize.core"},
        )
        self.metrics.on_llm_end(
            LLMResult(
                generations=[
                    [
                        ChatGeneration(
                            message=AIMessage(
                                content="fixture",
                                usage_metadata={
                                    "input_tokens": 250,
                                    "output_tokens": 25,
                                    "total_tokens": 275,
                                },
                            )
                        )
                    ]
                ]
            ),
            run_id=run_id,
        )
        raise RuntimeError("fixture structured output failure")


class _CheckpointState(TypedDict):
    completed: Annotated[list[str], operator.add]


class _ResumableGraph:
    first_calls = 0
    second_calls = 0
    fail_second_once = True

    def __init__(self, **_kwargs):
        pass

    def execute(
        self,
        context,
        *,
        checkpointer,
        checkpoint_thread_id,
        resume,
        on_event,
    ):
        def first_node(_state):
            type(self).first_calls += 1
            execution = _execution(context.request.ticker)
            context.artifact_writer(
                ResearchArtifactDraft(
                    node="fixture.first",
                    stage="analyst",
                    role="market",
                    generation_method=ArtifactGenerationMethod.TOOL_CALL,
                    content=execution.reports["market"],
                )
            )
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.first",
                    "payload": {},
                }
            )
            return {"completed": ["first"]}

        def second_node(_state):
            type(self).second_calls += 1
            if type(self).fail_second_once:
                type(self).fail_second_once = False
                raise RuntimeError("fixture crash after first checkpoint")
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.second",
                    "payload": {},
                }
            )
            return {"completed": ["second"]}

        workflow = StateGraph(_CheckpointState)
        workflow.add_node("first", first_node)
        workflow.add_node("second", second_node)
        workflow.add_edge(START, "first")
        workflow.add_edge("first", "second")
        workflow.add_edge("second", END)
        graph = workflow.compile(checkpointer=checkpointer)
        state = graph.invoke(
            None if resume else {"completed": []},
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        assert state["completed"] == ["first", "second"]
        return _execution(context.request.ticker)


class _CancellingCheckpointGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(
        self,
        context,
        *,
        checkpointer,
        checkpoint_thread_id,
        resume,
        on_event,
    ):
        def first_node(_state):
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.before_cancel",
                    "payload": {},
                }
            )
            return {"completed": ["first"]}

        def cancel_node(_state):
            raise RunCancelled("fixture cooperative cancellation")

        workflow = StateGraph(_CheckpointState)
        workflow.add_node("first", first_node)
        workflow.add_node("cancel", cancel_node)
        workflow.add_edge(START, "first")
        workflow.add_edge("first", "cancel")
        workflow.add_edge("cancel", END)
        graph = workflow.compile(checkpointer=checkpointer)
        graph.invoke(
            None if resume else {"completed": []},
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        raise AssertionError("cancelling graph must not complete")


@pytest.fixture(autouse=True)
def _reset_graph():
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True
    yield
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True


def _service(
    app_settings,
    repository: RunRepository,
    graph_factory=_Graph,
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=graph_factory,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
    )


def _complete_empty_manifest(plan: IncrementalCollectionPlan) -> CollectionManifest:
    return CollectionManifest(
        plan_version=plan.version,
        market=plan.market,
        entries=tuple(
            CollectionManifestEntry(
                domain=source.domain,
                source=source.source,
                provider_identity=source.provider_identity,
                chain_position=source.chain_position,
                retrieved_at=plan.window_end if source.configured else None,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                scanned_from=plan.window_start if source.configured else None,
                scanned_through=plan.window_end if source.configured else None,
                source_watermark="fixture-watermark" if source.configured else None,
                outcome=(
                    CollectionOutcome.COMPLETE_EMPTY
                    if source.configured
                    else CollectionOutcome.NOT_APPLICABLE
                ),
            )
            for source in plan.sources
        ),
    )


def _evidence_bearing_collection(
    plan: IncrementalCollectionPlan,
    candidate: IncrementalEvidenceCandidate,
) -> IncrementalCollectionResult:
    """One complete, market-neutral fake scan with exactly one new news record."""
    entries = []
    for source in plan.sources:
        has_record = source.configured and source.domain == "news"
        entries.append(
            CollectionManifestEntry(
                domain=source.domain,
                source=source.source,
                provider_identity=source.provider_identity,
                chain_position=source.chain_position,
                retrieved_at=plan.window_end if source.configured else None,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                scanned_from=plan.window_start if source.configured else None,
                scanned_through=plan.window_end if source.configured else None,
                source_watermark="fixture-watermark" if source.configured else None,
                outcome=(
                    CollectionOutcome.COMPLETE_WITH_RECORDS
                    if has_record
                    else (
                        CollectionOutcome.COMPLETE_EMPTY
                        if source.configured
                        else CollectionOutcome.NOT_APPLICABLE
                    )
                ),
                evidence_refs=(candidate.evidence.ref,) if has_record else (),
            )
        )
    return IncrementalCollectionResult(
        collection_manifest=CollectionManifest(
            plan_version=plan.version,
            market=plan.market,
            entries=tuple(entries),
        ),
        evidence=(candidate,),
    )


def _seed_baseline_evidence_ownership_collision(
    repository: RunRepository,
    baseline_run_id: str,
) -> EvidenceBundle:
    """Persist a sealed baseline payload identical to the final child candidate."""
    copied_item = EvidenceItem.create(
        source="fixture.news",
        evidence_type="late-disclosure",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 19),
        available_at=datetime(2026, 7, 22, 3, 59, 59, tzinfo=UTC),
        content="The exact sealed Evidence payload must remain baseline-owned.",
    )
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(copied_item,),
    )
    with repository.sessions.begin() as session:
        record = session.get(RunEvidenceRecord, baseline_run_id)
        assert record is not None
        record.bundle_json = evidence.model_dump(mode="json")
        record.digest = evidence.digest
        record.item_count = len(evidence.items)
        record.table_count = len(evidence.tables)
    return evidence


def _mutate_incremental_baseline(
    repository: RunRepository,
    baseline_run_id: str,
    mutation: str,
) -> None:
    repository.trash_runs((baseline_run_id,))
    if mutation == "trash":
        return
    assert mutation == "purge"
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, baseline_run_id)
        assert record is not None
        record.trashed_at = datetime(2020, 1, 1)
    assert repository.purge_expired_trash(
        cutoff=datetime(2020, 1, 2, tzinfo=UTC)
    ) == 1


def _incremental_synthesis_input_for_test(
    repository: RunRepository,
    baseline_run_id: str,
) -> IncrementalSynthesisInput:
    baseline = repository.get_run(baseline_run_id)
    result = repository.get_result(baseline_run_id)
    evidence = repository.get_evidence(baseline_run_id)
    assert result.decision is not None
    manifest = CollectionManifest(
        plan_version="1",
        market="united_states",
        entries=(
            CollectionManifestEntry(
                domain="news",
                source="fixture",
                provider_identity="fixture",
                retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
                planned_from=datetime(2026, 7, 21, tzinfo=UTC),
                planned_through=datetime(2026, 7, 24, tzinfo=UTC),
                scanned_from=datetime(2026, 7, 21, tzinfo=UTC),
                scanned_through=datetime(2026, 7, 24, tzinfo=UTC),
                source_watermark="fixture-watermark",
                outcome=CollectionOutcome.COMPLETE_EMPTY,
            ),
        ),
    )
    return IncrementalSynthesisInput(
        full_baseline_run_id=baseline_run_id,
        full_baseline_decision=result.decision,
        permitted_baseline_evidence_refs=tuple(item.ref for item in evidence.items),
        incremental_evidence=EvidenceBundle(
            instrument=baseline.request.ticker,
            analysis_date=date(2026, 7, 24),
            items=(),
        ),
        collection_manifest=manifest,
        research_coverage=ResearchCoverage(
            policy_version="1",
            domains=(
                ResearchCoverageDomain(
                    domain="news",
                    requirement=CoverageRequirement.REQUIRED,
                    status=CoverageStatus.COMPLETE,
                ),
            ),
        ),
        information_advancement=InformationAdvancement(
            advanced=True,
            reasons=("complete_empty_scan",),
        ),
        performance=PerformanceObservation(
            status="not_yet_observable",
            reason="Fixture interval is not observable.",
        ),
        method_snapshot=baseline.method_snapshot or {},
    )


class _PromptSpy:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def invoke(self, prompt, config=None):
        del config
        self.prompts.append(prompt)
        return self.response


class _StructuredPromptSpy:
    preferred_structured_output_method = "function_calling"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.valid = None

    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, prompt, config=None):
        del config
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return {"raw": None, "parsed": {}}
        value = self.valid(prompt) if callable(self.valid) else self.valid
        return {"raw": None, "parsed": value}


def test_service_persists_events_before_callback_and_result(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    seen = []

    def callback(event):
        persisted = repository.list_events(
            event.run_id,
            after_sequence=event.sequence - 1,
        )
        assert persisted[0] == event
        seen.append(event.event_type)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        ),
        on_event=callback,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.evidence is not None
    assert seen == [
        "run.started",
        "node.completed",
        "evidence.sealed",
        "run.succeeded",
    ]
    events = repository.list_events(result.run_id)
    assert events[0].event_type == "run.queued"
    assert events[-1].event_type == "run.succeeded"
    assert events[2].payload["api_key"] == "[REDACTED]"


def test_full_service_run_has_no_legacy_review_state(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.decision is not None
    with repository.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "outcomes" not in tables
    assert "reflections" not in tables


def test_rejected_creation_has_no_persistent_side_effects(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    invalid_request = AnalysisRequest.model_construct(
        ticker="BTC-USD",
        analysis_date=date(2026, 7, 24),
        asset_type="crypto",
    )

    with pytest.raises(
        ValueError,
        match="stock|Crypto instruments|listed equity",
    ):
        service.enqueue(invalid_request)

    table_names = (
        "runs",
        "run_attempts",
        "run_events",
        "run_evidence",
        "run_artifacts",
        "decisions",
        "checkpoints",
        "writes",
    )
    with repository.engine.connect() as connection:
        available_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts = {
            table: (
                connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
                if table in available_tables
                else 0
            )
            for table in table_names
        }

    assert counts == dict.fromkeys(table_names, 0)


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (
            {
                "short_name": "NVIDIA",
                "company_name": "NVIDIA Corporation",
                "long_name": "NVIDIA Corporation",
                "name": "Fallback name",
            },
            "NVIDIA",
        ),
        ({"company_name": "Toyota Motor Corporation"}, "Toyota Motor Corporation"),
        ({"long_name": "Mitsubishi Heavy Industries"}, "Mitsubishi Heavy Industries"),
        ({"name": "Fallback name"}, "Fallback name"),
        ({"short_name": " n/a ", "company_name": "  "}, None),
    ],
)
def test_service_persists_preferred_instrument_display_name(
    app_settings,
    repository,
    identity,
    expected,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda _ticker, _date: identity,
        eligibility_resolver=_equity_resolver,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.instrument_name == expected
    assert repository.get_run(result.run_id).instrument_name == expected
    assert repository.get_result(result.run_id).instrument_name == expected


def test_service_persists_cutoff_safe_local_name_once(
    app_settings,
    repository,
) -> None:
    observed: list[tuple[str, str]] = []

    def local_name(ticker, analysis_date, _config):
        observed.append((ticker, analysis_date))
        return "トヨタ自動車"

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda _ticker, _date: {"company_name": "Toyota Motor Corporation"},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=local_name,
    )

    result = service.run(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.instrument_local_name == "トヨタ自動車"
    assert repository.get_run(result.run_id).instrument_local_name == "トヨタ自動車"
    assert repository.get_result(result.run_id).instrument_local_name == "トヨタ自動車"
    assert observed == [("7203.T", "2026-07-24")]


def test_instrument_identity_failure_does_not_fail_research_run(
    app_settings,
    repository,
    caplog,
) -> None:
    def fail_identity(_ticker, _date):
        raise RuntimeError("private resolver details")

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=fail_identity,
        eligibility_resolver=_equity_resolver,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.instrument_name is None
    assert "RuntimeError" in caplog.text
    assert "private resolver details" not in caplog.text


def test_service_commits_artifact_and_event_before_callback(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ArtifactGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    observed = []

    def callback(event):
        if event.event_type != "artifact.created":
            return
        artifacts = repository.list_artifacts(event.run_id)
        assert [artifact.id for artifact in artifacts] == [event.payload["artifact_id"]]
        persisted = repository.list_events(
            event.run_id,
            after_sequence=event.sequence - 1,
        )
        assert persisted[0] == event
        observed.append(event)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        ),
        on_event=callback,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(observed) == 1
    assert set(observed[0].payload) == {
        "artifact_id",
        "attempt",
        "stage",
        "role",
        "round",
        "schema_version",
        "prompt_version",
        "generation_method",
        "generation_observations",
        "content_type",
    }


def test_artifact_persistence_failure_fails_attempt_and_retains_checkpoint(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ArtifactGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    def fail_write(*_args, **_kwargs):
        raise OSError("artifact database unavailable")

    monkeypatch.setattr(repository, "append_artifact", fail_write)

    with pytest.raises(OSError, match="artifact database unavailable"):
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_run(queued.id).status is RunStatus.FAILED
    assert repository.checkpoint_thread(queued.id) == checkpoint


def test_concurrent_runs_do_not_cross_provider_configuration(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.barrier = Barrier(2)
    requests = (
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
            llm_provider="openai",
        ),
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
            llm_provider="deepseek",
            output_language="ja",
        ),
    )
    claimed = []
    for index, request in enumerate(requests):
        queued = service.enqueue(request)
        claimed.append(repository.claim_run(queued.id, f"worker-{index}", 30))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: service.execute_claimed(
                    pair[1],
                    worker_id=f"worker-{pair[0]}",
                ),
                enumerate(claimed),
            )
        )

    assert {result.status for result in results} == {RunStatus.SUCCEEDED}
    assert set(_Graph.observed) == {
        ("NVDA", "openai", "openai"),
        ("7203.T", "deepseek", "deepseek"),
    }


def test_failure_is_redacted_and_checkpoint_is_retained(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RuntimeError("provider token=private-value")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    with pytest.raises(RuntimeError):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert "private-value" not in failed.error_message
    assert repository.checkpoint_thread(queued.id) == checkpoint


def test_snapshot_conversion_failure_fails_claimed_run_before_graph(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, queued.id)
        record.request_json = {
            **record.request_json,
            "ticker": "BTC-USD",
            "asset_type": "crypto",
        }
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(UnsupportedInstrumentError, match="not a supported listed equity"):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "UnsupportedInstrumentError"
    assert "not a supported listed equity" in failed.error_message
    assert repository.list_attempts(queued.id)[0].status is RunStatus.FAILED
    events = repository.list_events(queued.id)
    assert events[-1].event_type == "run.failed"
    assert events[-1].payload["error_code"] == "UnsupportedInstrumentError"
    assert [event.event_type for event in events] == [
        "run.queued",
        "run.started",
        "run.failed",
    ]


def test_failure_persists_observed_metrics_and_emits_the_aggregate(
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_MetricFailureGraph,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(RuntimeError, match="structured output failure"):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    event = repository.list_events(queued.id)[-1]
    assert failed.metrics.llm_calls == 1
    assert failed.metrics.input_tokens == 250
    assert failed.metrics.output_tokens == 25
    assert failed.metrics.node_metrics["analyst.market.serialize.core"].llm_calls == 1
    assert event.event_type == "run.failed"
    assert event.payload["metrics"]["input_tokens"] == 250

    _, json_body = service.export(queued.id, format="json")
    json_payload = json.loads(json_body)
    assert json_payload["run"]["metrics"]["input_tokens"] == 250
    assert json_payload["attempts"][0]["status"] == "failed"
    assert json_payload["attempts"][0]["metrics"]["input_tokens"] == 250

    _, markdown_body = service.export(queued.id, format="markdown")
    assert "## Performance" in markdown_body
    assert "| 1 | failed |" in markdown_body
    assert "Input tokens: `250`" in markdown_body

    _, package_body = service.export(queued.id, format="package")
    with zipfile.ZipFile(io.BytesIO(package_body)) as archive:
        run_payload = json.loads(archive.read("run.json"))
    assert run_payload["attempts"][0]["status"] == "failed"
    assert run_payload["run"]["metrics"]["input_tokens"] == 250


def test_cooperative_cancel_cleans_checkpoint(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RunCancelled("cancelled")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    cleared = []
    monkeypatch.setattr(service, "_clear_checkpoint", cleared.append)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.CANCELLED
    assert cleared == [repository.checkpoint_thread(queued.id)]
    assert repository.get_run(queued.id).status is RunStatus.CANCELLED


def test_worker_shutdown_requeues_run_and_preserves_checkpoint(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = WorkerShutdown("fixture shutdown")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    with pytest.raises(WorkerShutdown):
        service.execute_claimed(
            claimed,
            worker_id="worker",
            shutdown_requested=lambda: True,
        )

    released = repository.get_run(queued.id)
    assert released.status is RunStatus.QUEUED
    assert released.attempt == 1
    assert repository.checkpoint_thread(queued.id) == checkpoint
    assert repository.list_events(queued.id)[-1].event_type == "run.interrupted"


def test_queued_cancel_is_terminal_and_emits_event(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    queued = service.enqueue(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    cancelled = service.cancel(queued.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert repository.list_events(queued.id)[-1].event_type == "run.cancelled"


def test_retry_resumes_real_langgraph_checkpoint_and_success_cleans_it(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ResumableGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    checkpoint_thread = repository.checkpoint_thread(queued.id)
    claimed = repository.claim_run(queued.id, "worker-1", 30)

    with pytest.raises(RuntimeError, match="after first checkpoint"):
        service.execute_claimed(claimed, worker_id="worker-1")

    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        checkpoint_config = {"configurable": {"thread_id": checkpoint_thread}}
        assert saver.get_tuple(checkpoint_config) is not None

    retried = service.retry(queued.id)
    assert retried.attempt == 2
    assert repository.checkpoint_thread(queued.id) == checkpoint_thread
    claimed_again = repository.claim_run(queued.id, "worker-2", 30)
    result = service.execute_claimed(claimed_again, worker_id="worker-2")

    assert result.status is RunStatus.SUCCEEDED
    assert _ResumableGraph.first_calls == 1
    assert _ResumableGraph.second_calls == 2
    artifacts = repository.list_artifacts(queued.id)
    assert len(artifacts) == 1
    assert artifacts[0].attempt == 1
    assert "run.resumed" in {event.event_type for event in repository.list_events(queued.id)}
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        assert saver.get_tuple(checkpoint_config) is None


def test_cooperative_cancel_deletes_real_pending_checkpoint(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_CancellingCheckpointGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    checkpoint_thread = repository.checkpoint_thread(queued.id)
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.CANCELLED
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        assert saver.get_tuple({"configurable": {"thread_id": checkpoint_thread}}) is None


@pytest.mark.parametrize("format", ("markdown", "json", "package"))
def test_service_export_reads_the_durable_result(
    format,
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_ArtifactGraph,
    )
    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    repository.append_event(
        result.run_id,
        "node.output_retry",
        node="debate.agenda.serialize",
        payload={
            "method": "tool_call_recovered",
            "reason_code": "non_json_response",
            "validation_issues": ["schema.issues"],
        },
    )
    repository.append_event(
        result.run_id,
        "node.output_recovered",
        node="debate.agenda.serialize",
        payload={"method": "tool_call_recovered"},
    )

    media_type, body = service.export(result.run_id, format=format)

    expected_media_type = {
        "markdown": "text/markdown",
        "json": "application/json",
        "package": "application/zip",
    }[format]
    assert media_type.startswith(expected_media_type)
    if format == "package":
        assert isinstance(body, bytes)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            report = archive.read("report.md").decode()
            assert result.run_id in report
            assert "Fixture thesis" in report
            run_payload = json.loads(archive.read("run.json"))
            assert run_payload["attempts"][0]["status"] == "succeeded"
            assert run_payload["result"]["recoveries"][0]["node"] == ("debate.agenda.serialize")
            assert "## Structured Recoveries" in report
        return
    assert isinstance(body, str)
    assert result.run_id in body
    assert "Fixture thesis" in body
    if format == "json":
        payload = json.loads(body)
        assert payload["schema_version"] == "9"
        assert payload["run"]["id"] == result.run_id
        assert payload["attempts"][0]["status"] == "succeeded"
        assert payload["attempts"][0]["metrics"] == payload["run"]["metrics"]
        assert payload["result"]["evidence"] == payload["evidence"]
        assert payload["result"]["recoveries"][0]["initial_reason_code"] == ("non_json_response")
        assert payload["evidence"]["items"][0]["source"] == "fixture"
        assert payload["artifacts"][0]["stage"] == "analyst"
        content = payload["artifacts"][0]["content"]
        assert "Fixture report." in content["markdown"]
    else:
        assert "## Research Process" in body
        assert "### analyst · market · round 0" not in body
        assert "## Reports" in body
        assert "Fixture report." in body
        assert "_No deliberation artifacts were recorded for this run._" in body
        assert "## Warnings" in body
        assert "## Structured Recoveries" in body
        assert "`debate.agenda.serialize`" in body
        assert "## Sources" in body
        assert "### Attempts" in body
        assert "| 1 | succeeded |" in body
        assert "### E01" in body
        assert '"source": "fixture"' in body


def test_service_export_rejects_unknown_format(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    with pytest.raises(ValueError, match="markdown.*json"):
        service.export(result.run_id, format="pdf")
