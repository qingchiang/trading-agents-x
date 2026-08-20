"""Deterministic, market-local Incremental collection planning and gating."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

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
    EvidenceItem,
    IncrementalCollectionPlan,
    IncrementalCollectionPreflight,
    IncrementalCollectionResult,
    IncrementalCollectionSource,
    IncrementalEvidenceCandidate,
    InformationAdvancement,
    ResearchCoverage,
    ResearchCoverageDomain,
)

IncrementalCollector = Callable[
    [IncrementalCollectionPlan], CollectionManifest | IncrementalCollectionResult
]

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
_MARKET_TIMEZONES = {
    "united_states": ZoneInfo("America/New_York"),
    "japan": ZoneInfo("Asia/Tokyo"),
    "mainland_china": ZoneInfo("Asia/Shanghai"),
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
            chain_position=position,
            configured=True,
        )
        for position, vendor in enumerate(vendors)
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
                chain_position=source.chain_position,
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
    *,
    evidence_items: tuple[EvidenceItem, ...] | None = None,
) -> IncrementalCollectionPreflight:
    """Derive Coverage and Information Advancement without semantic work."""
    if manifest.plan_version != plan.version or manifest.market != plan.market:
        raise ValueError("Collection Manifest does not match its deterministic plan")
    if len(manifest.entries) != len(plan.sources):
        raise ValueError(
            "Collection Manifest contains an unconfigured source or does not "
            "exactly match its deterministic plan"
        )
    for source, entry in zip(plan.sources, manifest.entries, strict=True):
        if (
            entry.domain,
            entry.source,
            entry.provider_identity,
            entry.chain_position,
        ) != (
            source.domain,
            source.source,
            source.provider_identity,
            source.chain_position,
        ):
            raise ValueError(
                "Collection Manifest contains an unconfigured source or does not "
                "exactly match the ordered configured fallback chain"
            )
        if (
            entry.planned_from,
            entry.planned_through,
        ) != (
            plan.window_start,
            plan.window_end,
        ):
            raise ValueError(
                "Collection Manifest observations must use the frozen plan interval"
            )

    if evidence_items is not None:
        manifest_evidence_refs = {
            ref for entry in manifest.entries for ref in entry.evidence_refs
        }
        admitted_evidence_refs = {item.ref for item in evidence_items}
        if manifest_evidence_refs != admitted_evidence_refs:
            raise ValueError(
                "Collection Manifest evidence references must exactly match admitted Evidence"
            )

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


def admit_incremental_evidence(
    plan: IncrementalCollectionPlan,
    candidates: tuple[IncrementalEvidenceCandidate, ...],
) -> tuple[EvidenceItem, ...]:
    """Resolve and validate only new Evidence in the frozen half-open window."""
    zone = _MARKET_TIMEZONES[plan.market]
    normalized: dict[str, EvidenceItem] = {}
    for candidate in candidates:
        available_at = candidate.evidence.available_at
        if available_at is None:
            if candidate.available_on is None:
                raise ValueError("Incremental Evidence requires reliable availability")
            available_at = datetime.combine(
                candidate.available_on,
                time.max,
                tzinfo=zone,
            )
        elif available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("Incremental Evidence availability must include a timezone")
        resolved = candidate.evidence.model_copy(
            update={"available_at": available_at.astimezone(UTC)}
        )
        if not plan.window_start < resolved.available_at <= plan.window_end:
            raise ValueError(
                "Incremental Evidence availability must lie in the baseline-to-cutoff window"
            )
        previous = normalized.get(resolved.ref)
        if previous is not None and previous != resolved:
            raise ValueError("Incremental Evidence reference collides with a different payload")
        normalized[resolved.ref] = resolved
    return tuple(normalized.values())


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
