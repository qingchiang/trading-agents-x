"""Adapt producer observations to the existing four-domain collection contract."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, time
from zoneinfo import ZoneInfo

from tradingagents.application.contracts import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionSourceProvenance,
    IncrementalEvidenceCandidate,
)

from .financial_inputs import collect_financial_inputs
from .jp.jquants_sentiment import get_market_investor_flows
from .macro_panel import get_global_macro_panel
from .source_observations import SourceObservation, capture_observations


def observation_candidates(request, observations):
    zone = ZoneInfo(
        {
            "japan": "Asia/Tokyo",
            "mainland_china": "Asia/Shanghai",
            "united_states": "America/New_York",
        }[request.market]
    )
    candidates = []
    for observation in observations:
        if observation.available_on is not None:
            available = datetime.combine(observation.available_on, time.max, tzinfo=zone)
            observation = replace(observation, available_at=available)
        if observation.is_pit:
            if not request.window_start < observation.available_at <= request.window_end:
                continue
        else:
            age = (observation.retrieved_at.astimezone(zone).date() - request.analysis_cutoff).days
            if not 0 <= age <= request.near_live_max_age_days:
                continue
        candidates.append(
            IncrementalEvidenceCandidate(evidence=observation.evidence(request.analysis_cutoff))
        )
    return tuple(candidates)


def augment_domain(request, domain, observations):
    candidates = observation_candidates(request, observations)
    if not candidates:
        return domain, ()
    sources = list(domain.sources)
    bases = list(domain.temporal_bases)
    for candidate in candidates:
        origin = candidate.evidence.origins[0]
        sources.append(
            CollectionSourceProvenance(
                source=re.sub(r"[^a-z0-9_.-]+", "_", origin.source.casefold()).strip("_"),
                retrieved_at=datetime.fromisoformat(origin.retrieved_at),
                fallback=candidate.evidence.fallback,
            )
        )
        bases.append("pit" if candidate.evidence.available_at else "near_live_advisory")
    combined_sources = {}
    for source in sources:
        previous = combined_sources.get(source.source)
        if previous is not None:
            source = source.model_copy(
                update={
                    "retrieved_at": max(source.retrieved_at, previous.retrieved_at),
                    "fallback": source.fallback or previous.fallback,
                }
            )
        combined_sources[source.source] = source
    preserve_data = domain.state == "data" and all(c.evidence.available_at for c in candidates)
    return CollectionDomainResult(
        domain=domain.domain,
        state="data" if preserve_data else "partial",
        sources=tuple(combined_sources.values()),
        temporal_bases=tuple(dict.fromkeys(bases)),
        evidence_refs=domain.evidence_refs + tuple(c.evidence.ref for c in candidates),
        observed_from=domain.observed_from,
        observed_through=domain.observed_through,
        diagnostic=domain.diagnostic if preserve_data else CollectionDiagnostic(code="bounded_source_observations"),
    ), candidates


def append_financials(request, domain, routed):
    inputs = collect_financial_inputs(
        request.instrument,
        request.analysis_cutoff.isoformat(),
        route=routed,
        include_overview=False,
        stop_on_rate_limit=True,
    )
    return augment_domain(
        request, domain, [SourceObservation.load(o) for o in inputs["observations"]]
    )


def collect_professional_signals(request, fetch):
    results = fetch(request.instrument, request.analysis_cutoff.isoformat())
    observations = [o for result in results for o in result.observations]
    empty = CollectionDomainResult(
        domain="social",
        state="unavailable",
        diagnostic=CollectionDiagnostic(code="no_usable_professional_signals"),
    )
    return augment_domain(request, empty, observations)


def append_news_context(request, domain, routed):
    observations = []
    calls = [
        lambda: routed("get_global_news", request.analysis_cutoff.isoformat(),
                       (request.analysis_cutoff - request.baseline_analysis_cutoff).days,
                       _provenance=True, _stop_on_rate_limit=True),
        lambda: get_global_macro_panel(request.analysis_cutoff.isoformat()),
    ]
    if request.market == "japan":
        calls.append(lambda: get_market_investor_flows(request.instrument, request.analysis_cutoff.isoformat()))
    for call in calls:
        with capture_observations() as captured:
            try:
                call()
            except Exception:
                continue
            observations.extend(captured)
    return augment_domain(request, domain, observations)


def append_market_context(request, domain, series, routed):
    observations = []
    if series is not None:
        points = [p for p in series.points if request.window_start < p.completed_at <= request.window_end]
        if points:
            closes = [p.adjusted_close for p in points]
            peak = closes[0]
            drawdown = 0.0
            for close in closes:
                peak = max(peak, close)
                drawdown = min(drawdown, close / peak - 1)
            observations.append(SourceObservation(
                series.source, "market_interval", request.instrument,
                {"start_session": points[0].session.isoformat(), "end_session": points[-1].session.isoformat(),
                 "close_change": closes[-1] / closes[0] - 1, "min_close": min(closes),
                 "max_close": max(closes), "maximum_drawdown": drawdown,
                 "adjustment_basis": series.adjustment_basis, "completed_rows": len(points)},
                series.retrieved_at, effective_date=points[-1].session,
                available_at=points[-1].completed_at,
                fallback=series.fallback,
                timing="completed observations in the actual interval; one provider and adjustment basis",
            ))
    with capture_observations() as captured:
        try:
            routed("get_verified_market_snapshot", request.instrument,
                   request.analysis_cutoff.isoformat(), 5, _provenance=True, _stop_on_rate_limit=True)
        except Exception:
            pass
        else:
            observations.extend(captured)
    return augment_domain(request, domain, observations)


def dedupe_news_domains(domains, candidates):
    """Keep one source article across CN news and professional-signal domains."""
    from .news_quality import canonical_headline

    items = {c.evidence.ref: c for c in candidates}
    seen = set()
    output = {}
    for domain in sorted(domains, key=lambda d: d.domain != "news"):
        refs = []
        for ref in domain.evidence_refs:
            item = items[ref].evidence
            observation = item.provenance.get("observation", {})
            values = observation.get("values", {})
            key = None
            if observation.get("kind") == "news_article":
                record = values.get("link") or values.get("url")
                key = (observation.get("source"), record or (
                    canonical_headline(values.get("title", "")), observation.get("effective_date")
                ))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            refs.append(ref)
        updates = {"evidence_refs": tuple(dict.fromkeys(refs))}
        if domain.evidence_refs and not refs:
            updates.update(state="empty", temporal_bases=(), observed_from=None, observed_through=None,
                           diagnostic=CollectionDiagnostic(code="articles_already_in_news"))
        elif len(refs) < len(domain.evidence_refs):
            sources = {}
            for ref in refs:
                for origin in items[ref].evidence.origins:
                    previous = sources.get(origin.source)
                    stamp = datetime.fromisoformat(origin.retrieved_at)
                    sources[origin.source] = CollectionSourceProvenance(
                        source=origin.source,
                        fallback=origin.fallback or bool(previous and previous.fallback),
                        retrieved_at=max(stamp, previous.retrieved_at) if previous else stamp,
                    )
            updates["sources"] = tuple(sources.values())
            updates["temporal_bases"] = tuple(dict.fromkeys(
                "pit" if items[ref].evidence.available_at else "near_live_advisory" for ref in refs
            ))
        output[domain.domain] = CollectionDomainResult.model_validate({**domain.model_dump(), **updates})
    retained = {ref for domain in output.values() for ref in domain.evidence_refs}
    return [output[d.domain] for d in domains], [c for ref, c in items.items() if ref in retained]
