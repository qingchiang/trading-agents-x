"""Shared offline fixtures for cycles contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.support.factories import research_decision
from tradingagents.domain.common import CURRENT_RESEARCH_SCHEMA_VERSION, RunStatus
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.domain.runs import AnalysisRequest, AnalysisResult
from tradingagents.persistence.configuration import ConfigurationStore


def _commit_node(
    repository,
    app_settings,
    *,
    analysis_date: date,
    baseline_id: str | None = None,
    make_primary: bool | None = None,
):
    research_kind = "incremental" if baseline_id else "full"
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=analysis_date,
        research_kind=research_kind,
        full_baseline_run_id=baseline_id,
        make_primary=make_primary,
    )
    run, _ = repository.create_run(
        request,
        ConfigurationStore(app_settings)
        .resolve_request(request, require_initialized=False)[1]
        .snapshot(),
        research_schema_version=CURRENT_RESEARCH_SCHEMA_VERSION,
        information_cutoff_at=datetime.combine(analysis_date, datetime.max.time(), UTC),
        method_snapshot={
            "schema_version": CURRENT_RESEARCH_SCHEMA_VERSION,
            "llm_provider": "fixture",
        },
        incremental_input_fingerprint=(
            f"fingerprint-{analysis_date.isoformat()}" if baseline_id else None
        ),
    )
    repository.claim_run(run.id, "fixture", 30)
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture",
        requested_date=analysis_date,
        effective_date=analysis_date,
        content=run.id,
    )
    evidence = EvidenceBundle(instrument="NVDA", analysis_date=analysis_date, items=(item,))
    result = AnalysisResult(
        run_id=run.id,
        status=RunStatus.SUCCEEDED,
        instrument="NVDA",
        reports={},
        decision=research_decision(evidence_refs=(item.ref,)),
        evidence=evidence,
    )
    if baseline_id:
        from tradingagents.domain.incremental import IncrementalNodeProducts

        products = _warning_products()
        products.update(
            full_research_required_reasons=[],
            decision_outcome="updated",
            decision_outcome_reason="Fixture observation changes the decision.",
        )
        repository.complete_incremental(
            run.id,
            result,
            evidence=evidence,
            products=IncrementalNodeProducts.model_validate(products),
        )
    else:
        repository.seal_evidence(run.id, evidence)
        repository.complete(run.id, result, evidence=evidence)
    return repository.get_run(run.id)


def _warning_products() -> dict[str, object]:
    return {
        "collection_summary": {
            "version": "1",
            "market": "united_states",
            "domains": [
                {
                    "domain": "news",
                    "state": "empty",
                    "sources": [
                        {
                            "source": "fixture",
                            "retrieved_at": "2026-07-26T20:00:00Z",
                        }
                    ],
                }
            ],
        },
        "research_availability": {
            "version": "1",
            "domains": [{"domain": "news", "status": "missing"}],
        },
        "information_advancement": {
            "advanced": True,
            "reasons": ["completed_stock_session"],
            "observation_ids": [],
        },
        "performance": {
            "stock": {"status": "unavailable", "reason": "fixture"},
            "benchmarks": [],
        },
        "reassessment": {
            "entries": [
                {
                    "component_id": "thesis",
                    "disposition": "unresolved",
                    "reason": "fixture",
                    "evidence_refs": [],
                }
            ]
        },
        "full_research_required_reasons": [
            {
                "code": "attribution.unreliable",
                "message": "Fixture warning.",
                "origin": "semantic",
                "evidence_refs": [],
            }
        ],
    }
