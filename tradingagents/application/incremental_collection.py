"""Deterministic, market-local Incremental collection planning and gating."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tradingagents.dataflows.interface import (
    get_category_for_method,
    get_vendor,
    parse_vendor_chain,
)
from tradingagents.dataflows.symbol_utils import match_exchange_suffix, normalize_symbol

from .contracts import (
    CollectionManifest,
    CollectionManifestEntry,
    CollectionOutcome,
    CoverageRequirement,
    CoverageStatus,
    IncrementalCollectionPlan,
    IncrementalCollectionPreflight,
    IncrementalCollectionSource,
    InformationAdvancement,
    ResearchCoverage,
    ResearchCoverageDomain,
)

IncrementalCollector = Callable[[IncrementalCollectionPlan], CollectionManifest]

_MARKET_IDENTITIES = {
    ".T": ("japan", ".T"),
    ".SS": ("mainland_china", ".SS"),
    ".SZ": ("mainland_china", ".SZ"),
}
_DOMAIN_METHODS = {
    "fundamentals": "get_fundamentals",
    "market": "get_stock_data",
    "news": "get_news",
}


def incremental_market_identity(ticker: str) -> dict[str, str]:
    """Parse the supported market and routing suffix once for a frozen Run."""
    canonical_ticker = normalize_symbol(ticker)
    suffix = match_exchange_suffix(canonical_ticker, _MARKET_IDENTITIES)
    market, route_suffix = _MARKET_IDENTITIES.get(
        suffix,
        ("united_states", ""),
    )
    return {"market": market, "route_suffix": route_suffix}


def build_incremental_collection_plan(
    *,
    market_identity: Mapping[str, Any],
    data_routes: Mapping[str, Any],
    coverage_policy: Mapping[str, Any],
    window_start,
    window_end,
) -> IncrementalCollectionPlan:
    """Resolve configured vendor chains from the Run's frozen Method Snapshot."""
    market = market_identity.get("market")
    route_suffix = market_identity.get("route_suffix")
    if market not in {"united_states", "japan", "mainland_china"}:
        raise ValueError("Incremental collection requires a frozen supported market")
    if not isinstance(route_suffix, str):
        raise ValueError("Incremental collection requires a frozen route suffix")
    required_domains = tuple(coverage_policy["required_domains"])
    advisory_domains = tuple(coverage_policy["advisory_domains"])
    sources = tuple(
        source
        for domain in (*required_domains, *advisory_domains)
        for source in _sources_for_domain(domain, route_suffix, data_routes)
    )
    return IncrementalCollectionPlan(
        version=str(coverage_policy["version"]),
        market=market,
        window_start=window_start,
        window_end=window_end,
        required_domains=required_domains,
        advisory_domains=advisory_domains,
        sources=sources,
    )


def _sources_for_domain(
    domain: str,
    route_suffix: str,
    data_routes: Mapping[str, Any],
) -> tuple[IncrementalCollectionSource, ...]:
    if domain == "social":
        # Ticket 05 has no social adapter. Record the policy limitation instead
        # of inventing a vendor or silently switching to an unconfigured one.
        return (
            IncrementalCollectionSource(
                domain="social",
                source="social.not_configured",
                provider_identity="not_configured",
                configured=False,
            ),
        )
    method = _DOMAIN_METHODS[domain]
    category = get_category_for_method(method)
    raw_chain = get_vendor(category, method, route_suffix, dict(data_routes))
    vendors = parse_vendor_chain(raw_chain)
    if not vendors or "default" in vendors:
        raise ValueError(
            f"Incremental collection requires an explicit configured chain for {method}"
        )
    return tuple(
        IncrementalCollectionSource(
            domain=domain,
            source=f"{domain}.{vendor}",
            provider_identity=vendor,
            configured=True,
        )
        for vendor in vendors
    )


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
                domain=source.domain,
                source=source.source,
                provider_identity=source.provider_identity,
                planned_from=plan.window_start,
                planned_through=plan.window_end,
                outcome=(
                    CollectionOutcome.NOT_QUERIED
                    if source.configured
                    else CollectionOutcome.NOT_APPLICABLE
                ),
            )
            for source in plan.sources
        ),
    )


def assess_incremental_collection(
    plan: IncrementalCollectionPlan,
    manifest: CollectionManifest,
) -> IncrementalCollectionPreflight:
    """Derive Coverage and Information Advancement without semantic work."""
    if manifest.plan_version != plan.version or manifest.market != plan.market:
        raise ValueError("Collection Manifest does not match its deterministic plan")
    planned_sources = {
        (source.domain, source.source, source.provider_identity)
        for source in plan.sources
    }
    if any(
        (entry.domain, entry.source, entry.provider_identity) not in planned_sources
        for entry in manifest.entries
    ):
        raise ValueError("Collection Manifest contains an unconfigured source")

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
    if manifest.newly_reviewable_baseline_component_ids:
        advancement_reasons += ("newly_reviewable_baseline_component",)
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
            newly_reviewable_baseline_component_ids=(
                manifest.newly_reviewable_baseline_component_ids
            ),
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
    if entry.evidence_refs:
        return ("admissible_evidence",)
    return ()
