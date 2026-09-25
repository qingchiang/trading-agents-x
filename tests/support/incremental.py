"""Shared offline fixtures for incremental contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.research_helpers import default_incremental_synthesizer, stub_run_llms
from tests.support.service import _equity_resolver, _Graph
from tradingagents.application.service import AnalysisService
from tradingagents.domain.collection import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionSourceProvenance,
    CollectionSummary,
    IncrementalCollectionRequest,
    IncrementalEvidenceCandidate,
)
from tradingagents.domain.incremental import (
    IncrementalCollectionResult,
)


def _unavailable_domains(request: IncrementalCollectionRequest):
    return tuple(
        CollectionDomainResult(
            domain=domain,
            state="unavailable",
            diagnostic=CollectionDiagnostic(code="not_configured"),
        )
        for domain in request.enabled_domains
    )


def _sources(
    source: str,
    retrieved_at: datetime,
    *,
    fallback: bool = False,
) -> tuple[CollectionSourceProvenance, ...]:
    return (
        CollectionSourceProvenance(
            source=source,
            fallback=fallback,
            retrieved_at=retrieved_at,
        ),
    )


def _incremental_service(
    app_settings,
    repository,
    *,
    collector,
    synthesizer=default_incremental_synthesizer,
    eligibility_resolver=_equity_resolver,
    identity_resolver=lambda symbol, _date: {"company_name": symbol},
    local_name_resolver=lambda _ticker, _date, _config: None,
    now=lambda: datetime(2026, 7, 24, 20, tzinfo=UTC),
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=stub_run_llms,
        graph_factory=_Graph,
        identity_resolver=identity_resolver,
        eligibility_resolver=eligibility_resolver,
        local_name_resolver=local_name_resolver,
        incremental_collector=collector,
        incremental_synthesizer=synthesizer,
        now=now,
    )


def _pit_collection(
    request: IncrementalCollectionRequest,
    candidate: IncrementalEvidenceCandidate,
    *,
    domain: str = "news",
) -> IncrementalCollectionResult:
    domains = list(_unavailable_domains(request))
    index = request.enabled_domains.index(domain)
    domains[index] = CollectionDomainResult(
        domain=domain,
        state="data",
        sources=_sources(
            candidate.evidence.source,
            request.window_end,
            fallback=candidate.evidence.fallback,
        ),
        temporal_bases=("pit",),
        evidence_refs=(candidate.evidence.ref,),
    )
    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=tuple(domains),
        ),
        evidence=(candidate,),
    )
