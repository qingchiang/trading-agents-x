"""Japanese Incremental v1 normalization over configured Tokyo dataflows.

The collector deliberately stays at the existing router/assembler boundary.
It records observations the J-Quants, EDINET/TDnet/news, and fundamentals
paths actually returned; it neither creates a second provider registry nor
turns bounded Japanese archives into a completeness claim.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from functools import partial
from zoneinfo import ZoneInfo

from tradingagents.data.collection_progress import report_collection_progress
from tradingagents.data.context import DataRequestContext
from tradingagents.data.incremental_common import (
    CollectionUnavailable,
    bounded_empty,
    fundamentals_spans,
    is_empty,
    is_failure,
    merge_sources,
    origin_from_record,
    unavailable,
)
from tradingagents.data.incremental_inputs import (
    append_financials,
    append_market_context,
    append_news_context,
    collect_news_observations,
    collect_professional_signals,
)
from tradingagents.data.interface import route_to_vendor as _default_route_to_vendor
from tradingagents.data.jp.calendar import completed_market_date, is_tse_open
from tradingagents.data.market_signals import fetch_sentiment_signals
from tradingagents.domain.collection import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionResultState,
    CollectionSourceProvenance,
    CollectionSummary,
    CollectionTemporalBasis,
    IncrementalCollectionRequest,
    IncrementalEvidenceCandidate,
)
from tradingagents.domain.evidence import EvidenceItem, EvidenceOrigin
from tradingagents.domain.incremental import IncrementalCollectionResult
from tradingagents.domain.performance import MarketSeriesPoint, MarketSeriesResult
from tradingagents.domain.vendor_errors import VendorRateLimitError

_TOKYO = ZoneInfo("Asia/Tokyo")
DEFAULT_ROUTE_TO_VENDOR = _default_route_to_vendor


def collect_japan_incremental(
    request: IncrementalCollectionRequest,
    *,
    route_to_vendor: Callable[..., object] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    data_context: DataRequestContext,
) -> IncrementalCollectionResult:
    """Collect enabled Tokyo domains through their configured routes once each."""
    if request.market != "japan":
        raise ValueError("Japanese collection requires a Japan request")
    routed = partial(route_to_vendor or DEFAULT_ROUTE_TO_VENDOR, data_context=data_context)
    domains: list[CollectionDomainResult] = []
    evidence: list[IncrementalEvidenceCandidate] = []
    stock_series: MarketSeriesResult | None = None
    stock_series_evidence_ref: str | None = None

    for domain in request.enabled_domains:
        report_collection_progress(domain, "started")
        if domain == "market":
            result, candidate, stock_series = _collect_market(request, routed, now)
            result, extra = append_market_context(request, result, stock_series, routed)
            domains.append(result)
            if candidate is not None:
                evidence.append(candidate)
                stock_series_evidence_ref = candidate.evidence.ref
            evidence.extend(extra)
        elif domain == "news":
            result, candidates = _collect_news(request, routed, now)
            result, extra = append_news_context(request, result, routed, data_context=data_context)
            domains.append(result)
            evidence.extend(extra)
            evidence.extend(candidates)
        elif domain == "fundamentals":
            result, candidates = _collect_fundamentals(request, routed, now)
            result, extra = append_financials(request, result, routed, data_context=data_context)
            domains.append(result)
            evidence.extend((*candidates, *extra))
        elif domain == "social":
            result, candidates = collect_professional_signals(
                request, partial(fetch_sentiment_signals, data_context=data_context)
            )
            domains.append(result)
            evidence.extend(candidates)
        else:
            raise ValueError(f"unsupported Japanese collection domain: {domain}")

        report_collection_progress(domain, "completed")

    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version, market=request.market, domains=tuple(domains)
        ),
        evidence=tuple(evidence),
        stock_series=stock_series,
        stock_series_evidence_ref=stock_series_evidence_ref,
    )


def _collect_market(request, routed, now):
    source = None
    try:
        response = routed(
            "get_stock_data",
            request.instrument,
            completed_market_date(
                request.baseline_analysis_cutoff,
                now=request.window_start,
            ).isoformat(),
            request.analysis_cutoff.isoformat(),
            _stop_on_rate_limit=True,
            _require_adjusted=True,
        )
        source, body = _routed_source(response, now)
        series, omitted = _market_series(request, source, response.market_data)
        current = tuple(
            point
            for point in series.points
            if request.window_start < point.completed_at <= request.window_end
        )
        if not current:
            return (
                CollectionDomainResult(
                    domain="market",
                    state=CollectionResultState.EMPTY,
                    sources=(source,),
                    observed_from=series.points[0].completed_at,
                    observed_through=series.points[-1].completed_at,
                    diagnostic=CollectionDiagnostic(code="no_admissible_market_observation"),
                ),
                None,
                series,
            )
        point = current[-1]
        item = EvidenceItem.create(
            source=source.source,
            evidence_type="adjusted_close",
            requested_date=request.analysis_cutoff,
            effective_date=point.session,
            available_at=point.completed_at,
            value=point.adjusted_close,
            unit="currency",
            content=f"Provider-adjusted close for {request.instrument} on {point.session.isoformat()}.",
            fallback=source.fallback,
            origins=(_pit_origin(source, "adjusted_close", point.session),),
            provenance={"adjustment_basis": series.adjustment_basis},
        )
        return (
            CollectionDomainResult(
                domain="market",
                state=(CollectionResultState.PARTIAL if omitted else CollectionResultState.DATA),
                sources=(source,),
                observed_from=series.points[0].completed_at,
                observed_through=series.points[-1].completed_at,
                temporal_bases=(CollectionTemporalBasis.PIT,),
                evidence_refs=(item.ref,),
                diagnostic=(
                    CollectionDiagnostic(code="inadmissible_market_rows_omitted")
                    if omitted
                    else None
                ),
            ),
            IncrementalEvidenceCandidate(evidence=item),
            series,
        )
    except CollectionUnavailable as exc:
        return unavailable("market", exc.code, sources=(source,) if source else ()), None, None
    except VendorRateLimitError:
        raise
    except Exception:
        return (
            unavailable(
                "market",
                "market_route_failure",
                sources=(source,) if source else (),
            ),
            None,
            None,
        )


def _collect_news(request, routed, now):
    try:
        _response, collected = collect_news_observations(request, routed, now)
        return collected
    except VendorRateLimitError:
        raise
    except Exception:
        return unavailable("news", "news_route_failure"), ()


def _collect_fundamentals(request, routed, now):
    sources = ()
    try:
        response = routed(
            "get_fundamentals",
            request.instrument,
            request.analysis_cutoff.isoformat(),
            _stop_on_rate_limit=True,
        )
        sources, body = _routed_sources(response, now)
        if is_failure(body):
            return unavailable("fundamentals", "fundamentals_retrieval_failed", sources=sources), ()
        if is_empty(body):
            return bounded_empty("fundamentals", sources), ()
        spans = fundamentals_spans(response, body)
        candidates: list[IncrementalEvidenceCandidate] = []
        reported_sources: dict[str, CollectionSourceProvenance] = {}
        temporal_limited_sources: dict[str, CollectionSourceProvenance] = {}
        bases: list[CollectionTemporalBasis] = []
        for span in spans:
            if span.content is None or not span.records:
                continue
            span_sources = _sources_from_records(span.records, now)
            if span.temporal_scope == "unknown":
                for actual_source in span_sources:
                    temporal_limited_sources[actual_source.source] = actual_source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="unknown_fundamentals_temporal_scope"
                            )
                        }
                    )
                continue
            if span.temporal_scope == "live_only":
                if any(
                    _producer_retrieved_at(record.retrieved_at) is None for record in span.records
                ):
                    for actual_source in span_sources:
                        temporal_limited_sources[actual_source.source] = actual_source.model_copy(
                            update={
                                "diagnostic": CollectionDiagnostic(
                                    code="unreliable_live_fundamentals_retrieval_time"
                                )
                            }
                        )
                    continue
                source = span_sources[0]
                item = EvidenceItem.create(
                    source=source.source,
                    evidence_type="fundamentals_snapshot",
                    requested_date=request.analysis_cutoff,
                    content=span.content,
                    fallback=source.fallback,
                    origins=tuple(
                        origin_from_record(
                            record,
                            actual_source,
                            "fundamentals_snapshot",
                            temporal_scope="live_only",
                        )
                        for record, actual_source in zip(span.records, span_sources, strict=True)
                    ),
                )
                candidates.append(IncrementalEvidenceCandidate(evidence=item))
                for actual_source in span_sources:
                    reported_sources[actual_source.source] = actual_source.model_copy(
                        update={"diagnostic": CollectionDiagnostic(code="near_live_snapshot")}
                    )
                bases.append(CollectionTemporalBasis.NEAR_LIVE_ADVISORY)
                continue
            available_on = span.available_on
            if available_on is None:
                continue
            available_at = _market_day_end(available_on)
            if not request.window_start < available_at <= request.window_end:
                continue
            effective = span.effective_date or span.available_on
            source = span_sources[0]
            item = EvidenceItem.create(
                source=source.source,
                evidence_type="fundamentals_disclosure",
                requested_date=request.analysis_cutoff,
                effective_date=effective,
                content=span.content,
                fallback=source.fallback,
                origins=tuple(
                    origin_from_record(
                        record,
                        actual_source,
                        "fundamentals_disclosure",
                        temporal_scope="point_in_time",
                    )
                    for record, actual_source in zip(span.records, span_sources, strict=True)
                ),
            )
            candidates.append(
                IncrementalEvidenceCandidate(evidence=item, available_on=available_on)
            )
            reported_sources.update({source.source: source for source in span_sources})
            bases.append(CollectionTemporalBasis.PIT)
        if not candidates:
            summary_sources = merge_sources(sources, tuple(temporal_limited_sources.values()))
            temporal_code = _fundamentals_temporal_limitation_code(
                temporal_limited_sources.values()
            )
            return (
                CollectionDomainResult(
                    domain="fundamentals",
                    state=CollectionResultState.EMPTY,
                    sources=summary_sources,
                    diagnostic=CollectionDiagnostic(
                        code=temporal_code or "no_admissible_fundamentals_observation"
                    ),
                ),
                (),
            )
        temporal_bases = tuple(dict.fromkeys(bases))
        state = (
            CollectionResultState.DATA
            if temporal_bases == (CollectionTemporalBasis.PIT,) and not temporal_limited_sources
            else CollectionResultState.PARTIAL
        )
        return (
            CollectionDomainResult(
                domain="fundamentals",
                state=state,
                sources=merge_sources(
                    tuple(reported_sources.values()),
                    tuple(temporal_limited_sources.values()),
                ),
                temporal_bases=temporal_bases,
                evidence_refs=tuple(candidate.evidence.ref for candidate in candidates),
                diagnostic=(
                    None
                    if state is CollectionResultState.DATA
                    else CollectionDiagnostic(
                        code=(
                            "fundamentals_temporal_scope_limited"
                            if temporal_limited_sources
                            else (
                                "near_live_snapshot"
                                if temporal_bases == (CollectionTemporalBasis.NEAR_LIVE_ADVISORY,)
                                else "mixed_pit_and_near_live_fundamentals"
                            )
                        )
                    )
                ),
            ),
            tuple(candidates),
        )
    except CollectionUnavailable as exc:
        return unavailable("fundamentals", exc.code, sources=sources), ()
    except VendorRateLimitError:
        raise
    except Exception:
        return unavailable("fundamentals", "fundamentals_route_failure", sources=sources), ()


def _routed_source(response: object, now):
    sources, body = _routed_sources(response, now)
    if len(sources) != 1:
        raise CollectionUnavailable("ambiguous_market_source_provenance")
    return sources[0], body


def _routed_sources(response: object, now):
    if not isinstance(response.content, str):
        raise CollectionUnavailable("non_text_routed_response")
    if response.content.startswith(
        ("NO_DATA_AVAILABLE:", "DATA_UNAVAILABLE:", "LIVE_DATA_UNAVAILABLE:")
    ):
        raise CollectionUnavailable("routed_source_unavailable")
    records = response.provenance
    if not records:
        raise CollectionUnavailable("missing_actual_source_provenance")
    sources = _sources_from_records(records, now)
    return sources, response.content.strip()


def _sources_from_records(records, now):
    return tuple(
        CollectionSourceProvenance(
            source=_source_id(record.source),
            fallback="fallback vendor selected" in record.timing.casefold(),
            retrieved_at=_producer_retrieved_at(record.retrieved_at) or _aware_now(now),
        )
        for record in records
    )


def _market_series(request, source, data):
    if data is None or data.instrument.casefold() != request.instrument.casefold():
        raise CollectionUnavailable("market_instrument_mismatch")
    expected = {
        "jquants": ("jquants_split_dividend_adjusted_close", "jquants_adjustment_basis_unverified"),
        "yfinance": ("yfinance_auto_adjusted_close", "yfinance_adjustment_basis_unverified"),
    }
    if source.source not in expected:
        raise CollectionUnavailable("market_adjustment_basis_unverified")
    basis, diagnostic = expected[source.source]
    if data.adjustment_basis != basis:
        raise CollectionUnavailable(diagnostic)
    try:
        points, omitted = [], False
        for row in data.rows:
            session = date.fromisoformat(str(row["Date"]).strip())
            value = float(row.get("Close") or "nan")
            if not is_tse_open(session):
                omitted = True
                continue
            completed_at = _market_close_at(session)
            if (
                not math.isfinite(value)
                or value <= 0
                or session > request.analysis_cutoff
                or completed_at > request.window_end
            ):
                omitted = True
                continue
            points.append(
                MarketSeriesPoint(session=session, completed_at=completed_at, adjusted_close=value)
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectionUnavailable("market_series_malformed") from exc
    if not points:
        raise CollectionUnavailable("no_admissible_market_rows")
    return MarketSeriesResult(
        instrument=request.instrument,
        source=source.source,
        fallback=source.fallback,
        adjustment_basis=basis,
        retrieved_at=source.retrieved_at,
        points=tuple(points),
    ), omitted


def _fundamentals_temporal_limitation_code(sources):
    diagnostics = {source.diagnostic.code for source in sources if source.diagnostic}
    if diagnostics == {"unknown_fundamentals_temporal_scope"}:
        return "unknown_fundamentals_temporal_scope"
    if diagnostics == {"unreliable_live_fundamentals_retrieval_time"}:
        return "unreliable_live_fundamentals_retrieval_time"
    return "fundamentals_temporal_scope_unavailable" if diagnostics else None


def _source_id(value):
    """Fit established human provenance names into the public source identifier."""
    return re.sub(r"[^a-z0-9_.-]+", "_", value.casefold()).strip("_")


def _pit_origin(source, evidence_type, effective_date):
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        requested=effective_date.isoformat(),
        effective=effective_date.isoformat(),
        effective_date=effective_date,
        timing="market-date or publication-date filtered",
        retrieved_at=source.retrieved_at.isoformat().replace("+00:00", "Z"),
        fallback=source.fallback,
        temporal_scope="point_in_time",
    )


def _market_day_end(value):
    return datetime.combine(value, time.max, tzinfo=_TOKYO).astimezone(UTC)


def _market_close_at(value):
    if not is_tse_open(value):
        raise ValueError(f"{value.isoformat()} is not an eligible TSE session")
    return datetime.combine(value, time(17), tzinfo=_TOKYO).astimezone(UTC)


def _producer_retrieved_at(value):
    """Return an aware producer timestamp, never substituting collection time."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _aware_now(now):
    value = now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Japanese collection clock must include a timezone")
    return value
