"""Deterministic, market-local Incremental collection planning and gating."""

from __future__ import annotations

from collections.abc import Callable

from .contracts import (
    CollectionManifest,
    CollectionManifestEntry,
    CollectionOutcome,
    CoverageRequirement,
    CoverageStatus,
    IncrementalCollectionPlan,
    IncrementalCollectionPreflight,
    InformationAdvancement,
    ResearchCoverage,
    ResearchCoverageDomain,
)

IncrementalCollector = Callable[[IncrementalCollectionPlan], CollectionManifest]

_MARKET_SOURCES = {
    "united_states": {
        "fundamentals": "sec_companyfacts",
        "market": "us_market_series",
        "news": "us_ticker_news",
        "social": "us_social_sentiment",
    },
    "japan": {
        "fundamentals": "jquants_statements",
        "market": "jquants_market_series",
        "news": "japan_disclosures_news",
        "social": "japan_social_sentiment",
    },
    "mainland_china": {
        "fundamentals": "cninfo_disclosures",
        "market": "china_market_series",
        "news": "china_ticker_news",
        "social": "china_social_sentiment",
    },
}


def default_incremental_collector(plan: IncrementalCollectionPlan) -> CollectionManifest:
    """Emit the executable plan's truthful initial no-query observations.

    Ticket 05 owns the structured gate.  Tickets 06 and 07 will replace these
    observations with complete-empty and evidence-bearing adapter results.
    """
    return CollectionManifest(
        plan_version=plan.version,
        market=plan.market,
        entries=tuple(
            CollectionManifestEntry(
                domain=domain,
                source=source,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                outcome=CollectionOutcome.NOT_QUERIED,
            )
            for domain, source in _MARKET_SOURCES[plan.market].items()
        ),
    )


def assess_incremental_collection(
    plan: IncrementalCollectionPlan,
    manifest: CollectionManifest,
) -> IncrementalCollectionPreflight:
    """Derive Coverage and Information Advancement without semantic work."""
    if manifest.plan_version != plan.version or manifest.market != plan.market:
        raise ValueError("Collection Manifest does not match its deterministic plan")

    domains = tuple(
        _coverage_domain(
            domain,
            CoverageRequirement.REQUIRED,
            manifest.entries,
        )
        for domain in plan.required_domains
    ) + tuple(
        _coverage_domain(
            domain,
            CoverageRequirement.ADVISORY,
            manifest.entries,
        )
        for domain in plan.advisory_domains
    )
    advancement_reasons = tuple(
        dict.fromkeys(
            reason
            for entry in manifest.entries
            for reason in _advancement_reasons(entry)
        )
    )
    diagnostics = tuple(
        dict.fromkeys(
            entry.diagnostic
            for entry in manifest.entries
            if entry.diagnostic is not None
        )
    )
    return IncrementalCollectionPreflight(
        collection_manifest=manifest,
        research_coverage=ResearchCoverage(
            policy_version=plan.version,
            domains=domains,
        ),
        information_advancement=InformationAdvancement(
            advanced=bool(advancement_reasons),
            reasons=advancement_reasons,
        ),
        diagnostics=diagnostics,
    )


def _coverage_domain(
    domain: str,
    requirement: CoverageRequirement,
    entries: tuple[CollectionManifestEntry, ...],
) -> ResearchCoverageDomain:
    outcomes = {entry.outcome for entry in entries if entry.domain == domain}
    if outcomes and outcomes <= {CollectionOutcome.NOT_APPLICABLE}:
        status = CoverageStatus.NOT_APPLICABLE
    elif outcomes & {
        CollectionOutcome.COMPLETE_EMPTY,
        CollectionOutcome.COMPLETE_WITH_RECORDS,
    }:
        status = CoverageStatus.COMPLETE
    elif CollectionOutcome.PARTIAL in outcomes:
        status = CoverageStatus.LIMITED
    elif requirement is CoverageRequirement.REQUIRED:
        status = CoverageStatus.MISSING
    else:
        status = CoverageStatus.LIMITED
    return ResearchCoverageDomain(
        domain=domain,
        requirement=requirement,
        status=status,
    )


def _advancement_reasons(
    entry: CollectionManifestEntry,
) -> tuple[str, ...]:
    if entry.outcome is CollectionOutcome.COMPLETE_EMPTY:
        return ("complete_empty_scan",)
    if entry.outcome is CollectionOutcome.COMPLETE_WITH_RECORDS:
        return ("admissible_evidence",)
    return ()
