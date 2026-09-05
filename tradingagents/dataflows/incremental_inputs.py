"""Adapt producer observations to the existing four-domain collection contract."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from tradingagents.application.contracts import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionResultState,
    CollectionSourceProvenance,
    IncrementalEvidenceCandidate,
)
from tradingagents.provenance import extract_provenance, strip_provenance_markers

from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
    VendorTransportError,
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
            IncrementalEvidenceCandidate(evidence=observation.evidence(request.analysis_cutoff, instrument=request.instrument))
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
                diagnostic=(CollectionDiagnostic(code="news_cache_refresh_failed")
                            if "news cache refresh failed" in origin.timing else None),
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
                    "diagnostic": source.diagnostic or previous.diagnostic,
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


def retain_input_limitations(result, responses, *, failure_code=None, now=None):
    """Keep producer limitations without replacing admitted material's retrieval time."""
    domain, candidates = result
    sources = {source.source: source for source in domain.sources}
    limited = False
    for response in responses:
        for record in extract_provenance(response):
            timing = record.timing.casefold()
            code = next((code for token, code in (
                ("cache refresh failed", "news_cache_refresh_failed"),
                ("unavailable", "upstream_source_unavailable"),
                ("partial", "upstream_source_partial"),
                ("source_window_limited", "source_window_limited"),
                ("truncated_by_global_cap", "truncated_by_global_cap"),
            ) if token in timing), None)
            if code is None or "fallback vendor selected" in timing:
                continue
            limited = True
            name = re.sub(r"[^a-z0-9_.-]+", "_", record.source.casefold()).strip("_")
            previous = sources.get(name)
            if previous is not None:
                sources[name] = previous.model_copy(update={
                    "diagnostic": previous.diagnostic or CollectionDiagnostic(code=code),
                })
            else:
                try:
                    stamp = datetime.fromisoformat(record.retrieved_at or "")
                    if stamp.tzinfo is None:
                        raise ValueError("missing timezone")
                except ValueError:
                    stamp = now() if now else datetime.now(UTC)
                sources[name] = CollectionSourceProvenance(
                    source=name, retrieved_at=stamp, diagnostic=CollectionDiagnostic(code=code),
                )
    if limited or failure_code:
        diagnostic = domain.diagnostic
        if diagnostic is None or diagnostic.code == "bounded_source_observations" or not domain.sources:
            diagnostic = CollectionDiagnostic(code=failure_code or "upstream_inputs_limited")
        elif failure_code and failure_code not in diagnostic.code.split("."):
            diagnostic = CollectionDiagnostic(code=f"{diagnostic.code}.{failure_code}")
        domain = domain.model_copy(update={
            "sources": tuple(sources.values()),
            "state": CollectionResultState.PARTIAL if domain.evidence_refs else domain.state,
            "diagnostic": diagnostic,
        })
    return domain, candidates


def _input_failed(response):
    body = strip_provenance_markers(str(response)).strip().casefold()
    return body.startswith(("error", "failed")) or bool(
        re.fullmatch(r"<[^<>]*unavailable[^<>]*>", body)
    )


def _typed_vendor_failure_code(exc: Exception) -> str | None:
    """Return the stable, secret-free diagnostic for a typed vendor failure."""
    if isinstance(exc, VendorRateLimitError):
        return "rate_limited"
    if isinstance(exc, VendorTransportError):
        return "transport_failure"
    if isinstance(exc, VendorNotConfiguredError):
        return "not_configured"
    if isinstance(exc, NoMarketDataError):
        return "no_usable_data"
    return None


def _context_failure_code(domain, generic_code, typed_codes):
    codes = [generic_code, *dict.fromkeys(typed_codes)]
    if typed_codes and domain.diagnostic is not None and not domain.sources:
        codes.insert(0, domain.diagnostic.code)
    return ".".join(codes)


def append_financials(request, domain, routed):
    inputs = collect_financial_inputs(
        request.instrument,
        request.analysis_cutoff.isoformat(),
        route=routed,
        include_overview=False,
        stop_on_rate_limit=True,
    )
    responses = tuple(inputs["responses"].values())
    return retain_input_limitations(
        augment_domain(request, domain, [SourceObservation.load(o) for o in inputs["observations"]]),
        responses,
        failure_code="financial_inputs_partial" if any(map(_input_failed, responses)) else None,
    )


def collect_professional_signals(request, fetch):
    results = fetch(request.instrument, request.analysis_cutoff.isoformat())
    observations = [o for result in results for o in result.observations]
    empty = CollectionDomainResult(
        domain="social",
        state="unavailable",
        diagnostic=CollectionDiagnostic(code="no_usable_professional_signals"),
    )
    responses = [result.body for result in results]
    return retain_input_limitations(
        augment_domain(request, empty, observations), responses,
        failure_code="professional_signals_partial" if any(map(_input_failed, responses)) else None,
    )


def append_news_context(request, domain, routed):
    observations = []
    responses = []
    failed = False
    typed_failures = []
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
                response = call()
            except Exception as exc:
                failed = True
                code = _typed_vendor_failure_code(exc)
                if code is not None:
                    typed_failures.append(code)
                continue
            responses.append(response)
            failed = failed or _input_failed(response)
            observations.extend(captured)
    return retain_input_limitations(
        augment_domain(request, domain, observations), responses,
        failure_code=(
            _context_failure_code(domain, "news_context_partial", typed_failures)
            if failed
            else None
        ),
    )


def append_market_context(request, domain, series, routed):
    observations = []
    response = ""
    failed = False
    typed_failures = []
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
            response = routed("get_verified_market_snapshot", request.instrument,
                   request.analysis_cutoff.isoformat(), 5, _provenance=True, _stop_on_rate_limit=True)
        except Exception as exc:
            failed = True
            code = _typed_vendor_failure_code(exc)
            if code is not None:
                typed_failures.append(code)
        else:
            observations.extend(captured)
    snapshot_failed = failed or _input_failed(response)
    return retain_input_limitations(
        augment_domain(request, domain, observations), (response,),
        failure_code=(
            _context_failure_code(
                domain, "market_snapshot_unavailable", typed_failures
            )
            if snapshot_failed
            else None
        ),
    )


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
