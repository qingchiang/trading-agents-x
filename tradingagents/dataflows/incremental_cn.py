"""Mainland-China Incremental v1 normalization over configured A-share dataflows."""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from io import StringIO
from zoneinfo import ZoneInfo

from tradingagents.application.contracts import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionResultState,
    CollectionSourceProvenance,
    CollectionSummary,
    CollectionTemporalBasis,
    EvidenceItem,
    EvidenceOrigin,
    IncrementalCollectionRequest,
    IncrementalCollectionResult,
    IncrementalEvidenceCandidate,
    MarketSeriesPoint,
    MarketSeriesResult,
)
from tradingagents.dataflows.cn import calendar
from tradingagents.dataflows.errors import VendorRateLimitError
from tradingagents.dataflows.incremental_inputs import (
    append_financials,
    append_market_context,
    append_news_context,
    augment_domain,
    collect_professional_signals,
)
from tradingagents.dataflows.interface import route_to_vendor as _default_route_to_vendor
from tradingagents.dataflows.market_signals import fetch_sentiment_signals
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope
from tradingagents.dataflows.source_observations import capture_observations
from tradingagents.provenance import (
    EvidenceSpan,
    extract_evidence_spans,
    extract_provenance,
    strip_provenance_markers,
    temporal_scope_from_records,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_ROUTE_TO_VENDOR = _default_route_to_vendor
_NEWS_ITEM = re.compile(
    r"^### (?P<title>.+?)\n(?P<body>.*?)(?=^### |\Z)",
    re.MULTILINE | re.DOTALL,
)
_PUBLISHED_AT = re.compile(
    r"^(?P<label>Disclosed|Published):\s*(?P<value>\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2}|\s+CST)?)?)",
    re.MULTILINE,
)
_EFFECTIVE_DATE = re.compile(
    r"^Effective period:\s*(?P<value>\d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)
_FUNDAMENTALS_VISIBLE = re.compile(
    r"^Latest visible disclosure/update:\s*(?P<value>\d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)


class _Unavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def collect_mainland_china_incremental(
    request: IncrementalCollectionRequest,
    *,
    route_to_vendor: Callable[..., object] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IncrementalCollectionResult:
    """Collect enabled mainland domains through their configured routes once each."""
    if request.market != "mainland_china":
        raise ValueError("Mainland-China collection requires a mainland-China request")
    routed = route_to_vendor or DEFAULT_ROUTE_TO_VENDOR
    domains: list[CollectionDomainResult] = []
    evidence: list[IncrementalEvidenceCandidate] = []
    stock_series: MarketSeriesResult | None = None
    stock_series_evidence_ref: str | None = None

    for domain in request.enabled_domains:
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
            result, extra = append_news_context(request, result, routed)
            domains.append(result)
            evidence.extend(extra)
            evidence.extend(candidates)
        elif domain == "fundamentals":
            result, candidates = _collect_fundamentals(request, routed, now)
            result, extra = append_financials(request, result, routed)
            domains.append(result)
            evidence.extend((*candidates, *extra))
        elif domain == "social":
            result, candidates = collect_professional_signals(request, fetch_sentiment_signals)
            domains.append(result)
            evidence.extend(candidates)
        else:
            raise ValueError(f"unsupported mainland-China collection domain: {domain}")

    from .incremental_inputs import dedupe_news_domains

    domains, evidence = dedupe_news_domains(domains, evidence)
    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=tuple(domains),
        ),
        evidence=tuple(evidence),
        stock_series=stock_series,
        stock_series_evidence_ref=stock_series_evidence_ref,
    )


def _collect_market(request, routed, now):
    source = None
    try:
        with stop_on_rate_limit_scope(True):
            response = routed(
                "get_stock_data",
                request.instrument,
                calendar.effective_trade_date(
                    request.baseline_analysis_cutoff,
                    now=request.window_start,
                ).isoformat(),
                request.analysis_cutoff.isoformat(),
                _provenance=True,
                _stop_on_rate_limit=True,
            )
        source, body = _routed_source(response, now)
        series, omitted = _market_series(request, source, body)
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
                    diagnostic=CollectionDiagnostic(
                        code="no_admissible_market_observation"
                    ),
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
            content=(
                f"Provider-adjusted close for {request.instrument} "
                f"on {point.session.isoformat()}."
            ),
            fallback=source.fallback,
            origins=(_pit_origin(source, "adjusted_close", point.session),),
            provenance={"adjustment_basis": series.adjustment_basis},
        )
        return (
            CollectionDomainResult(
                domain="market",
                state=(
                    CollectionResultState.PARTIAL
                    if omitted
                    else CollectionResultState.DATA
                ),
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
    except _Unavailable as exc:
        return _unavailable(
            "market", exc.code, sources=(source,) if source else ()
        ), None, None
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable(
            "market",
            "market_route_failure",
            sources=(source,) if source else (),
        ), None, None


def _collect_news(request, routed, now):
    sources = ()
    try:
        with capture_observations() as news_observations:
            response = routed(
                "get_news",
                request.instrument,
                request.baseline_analysis_cutoff.isoformat(),
                request.analysis_cutoff.isoformat(),
                _provenance=True,
                _stop_on_rate_limit=True,
            )
        if news_observations:
            from dataclasses import replace

            fallback = any("fallback vendor selected" in r.timing for r in extract_provenance(response))
            return augment_domain(request, CollectionDomainResult(
                domain="news", state="unavailable",
                diagnostic=CollectionDiagnostic(code="bounded_no_admitted_articles"),
            ), [replace(o, fallback=o.fallback or fallback) for o in news_observations])
        sources, body = _routed_sources(response, now)

        if _is_failure(body):
            return _unavailable(
                "news", "news_retrieval_failed", sources=sources
            ), ()
        records = extract_provenance(response)
        limited_sources = _news_availability_sources(records, now)
        cap_limited_sources = _news_global_cap_sources(records, now)
        if _is_empty(body):
            return _bounded_empty(
                "news",
                _merge_sources(sources, limited_sources, cap_limited_sources),
            ), ()

        candidates: list[IncrementalEvidenceCandidate] = []
        observed = []
        used_sources: dict[str, CollectionSourceProvenance] = {}
        temporal_limited_sources: dict[str, CollectionSourceProvenance] = {}
        bases: list[CollectionTemporalBasis] = []
        for span in _news_spans(response, body):
            if span.records and all(
                _is_news_availability_record(record) for record in span.records
            ):
                continue
            if span.content is None or len(span.records) != 1:
                if span.content and span.records:
                    raise _Unavailable("unbound_news_item_provenance")
                continue
            source = _source_from_record(span.records[0], now)
            if span.temporal_scope == "unknown":
                temporal_limited_sources[source.source] = source.model_copy(
                    update={
                        "diagnostic": CollectionDiagnostic(
                            code="unknown_news_temporal_scope"
                        )
                    }
                )
                continue
            if span.temporal_scope == "live_only":
                record = span.records[0]
                if _producer_retrieved_at(record.retrieved_at) is None:
                    temporal_limited_sources[source.source] = source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="unreliable_live_news_retrieval_time"
                            )
                        }
                    )
                    continue
                origin = _origin_from_record(
                    record,
                    source,
                    "disclosure_or_news",
                    temporal_scope="live_only",
                )
                for match in _NEWS_ITEM.finditer(span.content):
                    content = (
                        f"{match.group('title').strip()}\n"
                        f"{match.group('body').strip()}"
                    ).strip()
                    item = EvidenceItem.create(
                        source=source.source,
                        evidence_type="disclosure_or_news",
                        requested_date=request.analysis_cutoff,
                        content=content,
                        fallback=source.fallback,
                        origins=(origin,),
                    )
                    candidates.append(IncrementalEvidenceCandidate(evidence=item))
                    used_sources[source.source] = source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="near_live_snapshot"
                            )
                        }
                    )
                bases.append(CollectionTemporalBasis.NEAR_LIVE_ADVISORY)
                continue

            origin = _origin_from_record(
                span.records[0],
                source,
                "disclosure_or_news",
                temporal_scope="point_in_time",
            )
            for match in _NEWS_ITEM.finditer(span.content):
                content = (
                    f"{match.group('title').strip()}\n"
                    f"{match.group('body').strip()}"
                ).strip()
                available_at, available_on = _publication_time(content)
                if available_at is not None:
                    if not request.window_start < available_at <= request.window_end:
                        continue
                elif available_on is not None:
                    conservative = _market_day_end(available_on)
                    if not request.window_start < conservative <= request.window_end:
                        continue
                else:
                    continue
                item = EvidenceItem.create(
                    source=source.source,
                    evidence_type="disclosure_or_news",
                    requested_date=request.analysis_cutoff,
                    effective_date=_effective_date(content),
                    available_at=available_at,
                    content=content,
                    fallback=source.fallback,
                    origins=(origin,),
                )
                candidates.append(
                    IncrementalEvidenceCandidate(
                        evidence=item,
                        available_on=(
                            available_on if available_at is None else None
                        ),
                    )
                )
                observed.append(available_at or _market_day_end(available_on))
                used_sources[source.source] = source
                bases.append(CollectionTemporalBasis.PIT)

        if not candidates:
            summary_sources = _merge_sources(
                sources,
                limited_sources,
                cap_limited_sources,
                tuple(temporal_limited_sources.values()),
            )
            return (
                CollectionDomainResult(
                    domain="news",
                    state=CollectionResultState.EMPTY,
                    sources=summary_sources,
                    diagnostic=CollectionDiagnostic(
                        code=(
                            "bounded_mainland_news_feed_with_upstream_unavailable"
                            if limited_sources
                            else (
                                "bounded_mainland_news_feed_with_global_cap"
                                if cap_limited_sources
                                else "no_reliably_dated_mainland_records"
                            )
                        )
                    ),
                ),
                (),
            )
        summary_sources = _merge_sources(
            tuple(used_sources.values()),
            limited_sources,
            cap_limited_sources,
            tuple(temporal_limited_sources.values()),
        )
        return (
            CollectionDomainResult(
                domain="news",
                state=CollectionResultState.PARTIAL,
                sources=summary_sources,
                observed_from=min(observed) if observed else None,
                observed_through=max(observed) if observed else None,
                temporal_bases=tuple(dict.fromkeys(bases)),
                evidence_refs=tuple(
                    candidate.evidence.ref for candidate in candidates
                ),
                diagnostic=CollectionDiagnostic(
                    code=(
                        "bounded_mainland_news_feed_with_upstream_unavailable"
                        if limited_sources
                        else (
                            "bounded_mainland_news_feed_with_global_cap"
                            if cap_limited_sources
                            else "bounded_mainland_news_feed"
                        )
                    )
                ),
            ),
            tuple(candidates),
        )
    except _Unavailable as exc:
        return _unavailable("news", exc.code, sources=sources), ()
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable("news", "news_route_failure", sources=sources), ()


def _collect_fundamentals(request, routed, now):
    sources = ()
    try:
        response = routed(
            "get_fundamentals",
            request.instrument,
            request.analysis_cutoff.isoformat(),
            _provenance=True,
            _stop_on_rate_limit=True,
        )
        sources, body = _routed_sources(response, now)
        if _is_failure(body):
            return _unavailable(
                "fundamentals",
                "fundamentals_retrieval_failed",
                sources=sources,
            ), ()
        if _is_empty(body):
            return _bounded_empty("fundamentals", sources), ()

        candidates: list[IncrementalEvidenceCandidate] = []
        reported_sources: dict[str, CollectionSourceProvenance] = {}
        temporal_limited_sources: dict[str, CollectionSourceProvenance] = {}
        bases: list[CollectionTemporalBasis] = []
        for span in _fundamentals_spans(response, body):
            if span.content is None or not span.records:
                continue
            span_sources = tuple(
                _source_from_record(record, now) for record in span.records
            )
            if span.temporal_scope == "unknown":
                for actual_source in span_sources:
                    temporal_limited_sources[
                        actual_source.source
                    ] = actual_source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="unknown_fundamentals_temporal_scope"
                            )
                        }
                    )
                continue
            if span.temporal_scope == "live_only":
                if any(
                    _producer_retrieved_at(record.retrieved_at) is None
                    for record in span.records
                ):
                    for actual_source in span_sources:
                        temporal_limited_sources[
                            actual_source.source
                        ] = actual_source.model_copy(
                            update={
                                "diagnostic": CollectionDiagnostic(
                                    code=(
                                        "live_snapshot_not_collected"
                                        if "not queried" in span.content.casefold()
                                        else "unreliable_live_fundamentals_retrieval_time"
                                    )
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
                        _origin_from_record(
                            record,
                            actual_source,
                            "fundamentals_snapshot",
                            temporal_scope="live_only",
                        )
                        for record, actual_source in zip(
                            span.records, span_sources, strict=True
                        )
                    ),
                )
                candidates.append(IncrementalEvidenceCandidate(evidence=item))
                for actual_source in span_sources:
                    reported_sources[
                        actual_source.source
                    ] = actual_source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="near_live_snapshot"
                            )
                        }
                    )
                bases.append(CollectionTemporalBasis.NEAR_LIVE_ADVISORY)
                continue

            if _span_is_unavailable(span):
                for actual_source in span_sources:
                    temporal_limited_sources[
                        actual_source.source
                    ] = actual_source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="pit_fundamentals_source_unavailable"
                            )
                        }
                    )
                continue
            available_on = _fundamentals_available_on(span.records, span.content)
            if available_on is None:
                for actual_source in span_sources:
                    temporal_limited_sources[
                        actual_source.source
                    ] = actual_source.model_copy(
                        update={
                            "diagnostic": CollectionDiagnostic(
                                code="unreliable_fundamentals_publication_date"
                            )
                        }
                    )
                continue
            available_at = _market_day_end(available_on)
            if not request.window_start < available_at <= request.window_end:
                continue
            effective = _effective_date(span.content)
            source = span_sources[0]
            item = EvidenceItem.create(
                source=source.source,
                evidence_type="fundamentals_disclosure",
                requested_date=request.analysis_cutoff,
                effective_date=effective,
                content=span.content,
                fallback=source.fallback,
                origins=tuple(
                    _origin_from_record(
                        record,
                        actual_source,
                        "fundamentals_disclosure",
                        temporal_scope="point_in_time",
                    )
                    for record, actual_source in zip(
                        span.records, span_sources, strict=True
                    )
                ),
            )
            candidates.append(
                IncrementalEvidenceCandidate(
                    evidence=item,
                    available_on=available_on,
                )
            )
            reported_sources.update(
                {source.source: source for source in span_sources}
            )
            bases.append(CollectionTemporalBasis.PIT)

        if not candidates:
            summary_sources = _merge_sources(
                sources, tuple(temporal_limited_sources.values())
            )
            return (
                CollectionDomainResult(
                    domain="fundamentals",
                    state=CollectionResultState.EMPTY,
                    sources=summary_sources,
                    diagnostic=CollectionDiagnostic(
                        code=(
                            _fundamentals_temporal_limitation_code(
                                temporal_limited_sources.values()
                            )
                            or "no_admissible_fundamentals_observation"
                        )
                    ),
                ),
                (),
            )
        temporal_bases = tuple(dict.fromkeys(bases))
        state = (
            CollectionResultState.DATA
            if temporal_bases == (CollectionTemporalBasis.PIT,)
            and not temporal_limited_sources
            else CollectionResultState.PARTIAL
        )
        return (
            CollectionDomainResult(
                domain="fundamentals",
                state=state,
                sources=_merge_sources(
                    tuple(reported_sources.values()),
                    tuple(temporal_limited_sources.values()),
                ),
                temporal_bases=temporal_bases,
                evidence_refs=tuple(
                    candidate.evidence.ref for candidate in candidates
                ),
                diagnostic=(
                    None
                    if state is CollectionResultState.DATA
                    else CollectionDiagnostic(
                        code=(
                            "fundamentals_temporal_scope_limited"
                            if temporal_limited_sources
                            else (
                                "near_live_snapshot"
                                if temporal_bases
                                == (CollectionTemporalBasis.NEAR_LIVE_ADVISORY,)
                                else "mixed_pit_and_near_live_fundamentals"
                            )
                        )
                    )
                ),
            ),
            tuple(candidates),
        )
    except _Unavailable as exc:
        return _unavailable("fundamentals", exc.code, sources=sources), ()
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable(
            "fundamentals", "fundamentals_route_failure", sources=sources
        ), ()


def _routed_source(response: object, now):
    sources, body = _routed_sources(response, now)
    if len(sources) != 1:
        raise _Unavailable("ambiguous_market_source_provenance")
    return sources[0], body


def _routed_sources(response: object, now):
    if not isinstance(response, str):
        raise _Unavailable("non_text_routed_response")
    if response.startswith(
        ("NO_DATA_AVAILABLE:", "DATA_UNAVAILABLE:", "LIVE_DATA_UNAVAILABLE:")
    ):
        raise _Unavailable("routed_source_unavailable")
    records = extract_provenance(response)
    if not records:
        raise _Unavailable("missing_actual_source_provenance")
    return (
        tuple(_source_from_record(record, now) for record in records),
        strip_provenance_markers(response).strip(),
    )


def _source_from_record(record, now):
    timing = record.timing.casefold()
    return CollectionSourceProvenance(
        source=_source_id(record.source),
        fallback=(
            "fallback vendor selected" in timing
            or "fallback:" in timing
        ),
        retrieved_at=_producer_retrieved_at(record.retrieved_at) or _aware_now(now),
    )


def _publication_time(content):
    timestamp = _PUBLISHED_AT.search(content)
    if timestamp is None:
        return None, None
    value = timestamp.group("value")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None, date.fromisoformat(value)
    rendered = value.replace("CST", "+08:00").strip()
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Unavailable("invalid_mainland_publication_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Unavailable("invalid_mainland_publication_time")
    available_at = parsed.astimezone(UTC)
    return available_at, available_at.astimezone(_SHANGHAI).date()


def _effective_date(content):
    match = _EFFECTIVE_DATE.search(content)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group("value"))
    except ValueError as exc:
        raise _Unavailable("invalid_mainland_effective_period") from exc


def _news_spans(response, body):
    spans = extract_evidence_spans(response)
    if spans:
        return tuple(spans)
    records = tuple(extract_provenance(response))
    selected_fallback = tuple(
        record
        for record in records
        if "fallback vendor selected" in record.timing.casefold()
    )
    if selected_fallback:
        return (
            EvidenceSpan(
                content=body,
                records=selected_fallback,
                temporal_scope=temporal_scope_from_records(selected_fallback),
            ),
        )
    return (
        EvidenceSpan(
            content=body,
            records=records,
            temporal_scope=temporal_scope_from_records(records),
        ),
    )


def _fundamentals_spans(response, body):
    spans = extract_evidence_spans(response)
    if spans:
        return tuple(spans)
    records = tuple(extract_provenance(response))
    return (
        EvidenceSpan(
            content=body,
            records=records,
            temporal_scope=temporal_scope_from_records(records),
        ),
    )


def _span_is_unavailable(span):
    return all("unavailable" in record.timing.casefold() for record in span.records)


def _fundamentals_available_on(records, body):
    dates = []
    visible = _FUNDAMENTALS_VISIBLE.search(body)
    if visible is not None:
        dates.append(date.fromisoformat(visible.group("value")))
    for record in records:
        timing = record.timing.casefold()
        if "publication" not in timing and "disclosure-date" not in timing:
            continue
        try:
            dates.append(date.fromisoformat(record.effective))
        except ValueError:
            continue
    return max(dates) if dates else None


def _fundamentals_temporal_limitation_code(sources):
    diagnostics = {
        source.diagnostic.code for source in sources if source.diagnostic
    }
    if diagnostics == {"unknown_fundamentals_temporal_scope"}:
        return "unknown_fundamentals_temporal_scope"
    if diagnostics == {"unreliable_live_fundamentals_retrieval_time"}:
        return "unreliable_live_fundamentals_retrieval_time"
    if diagnostics == {"live_snapshot_not_collected"}:
        return "live_snapshot_not_collected"
    if diagnostics == {"unreliable_fundamentals_publication_date"}:
        return "unreliable_fundamentals_publication_date"
    return "fundamentals_temporal_scope_unavailable" if diagnostics else None


def _news_availability_sources(records, now):
    unavailable = {}
    for record in records:
        if not _is_news_availability_record(record):
            continue
        source = _source_from_record(record, now)
        unavailable[source.source] = source.model_copy(
            update={
                "diagnostic": CollectionDiagnostic(
                    code="upstream_source_unavailable"
                )
            }
        )
    return tuple(unavailable.values())


def _news_global_cap_sources(records, now):
    omitted = {}
    for record in records:
        timing = record.timing.casefold()
        if "truncated_by_global_cap=" not in timing or "kept_items=0" not in timing:
            continue
        source = _source_from_record(record, now)
        omitted[source.source] = source.model_copy(
            update={
                "diagnostic": CollectionDiagnostic(
                    code="truncated_by_global_cap"
                )
            }
        )
    return tuple(omitted.values())


def _is_news_availability_record(record):
    timing = record.timing.casefold()
    return "fallback vendor selected" not in timing and "unavailable" in timing


def _merge_sources(*source_groups):
    merged = {}
    for group in source_groups:
        for source in group:
            merged[source.source] = source
    return tuple(merged.values())


def _origin_from_record(record, source, evidence_type, *, temporal_scope):
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        requested=record.requested or "unknown",
        effective=record.effective or "unknown",
        effective_date=_origin_effective_date(record.effective),
        timing=record.timing or "unknown",
        retrieved_at=(
            record.retrieved_at
            or source.retrieved_at.isoformat().replace("+00:00", "Z")
        ),
        fallback=source.fallback,
        temporal_scope=temporal_scope,
    )


def _origin_effective_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _bounded_empty(domain, sources):
    return CollectionDomainResult(
        domain=domain,
        state=CollectionResultState.EMPTY,
        sources=sources,
        diagnostic=CollectionDiagnostic(code="bounded_feed_no_observed_records"),
    )


def _is_empty(body):
    lowered = body.strip().casefold()
    return not lowered or lowered.startswith("no ")


def _is_failure(body):
    return body.strip().casefold().startswith(
        ("error fetching", "error retrieving", "error getting")
    )


def _market_series(request, source, body):
    match = re.search(r"^# Stock data for (?P<instrument>.+?) from ", body, re.MULTILINE)
    if (
        match is None
        or match.group("instrument").strip().casefold()
        != request.instrument.casefold()
    ):
        raise _Unavailable("market_instrument_mismatch")
    header = body.casefold()
    if source.source in {"akshare_tencent", "akshare_eastmoney"}:
        if "qfq (forward-adjusted)" not in header:
            raise _Unavailable("mainland_qfq_basis_unverified")
        basis = "qfq_forward_adjusted"
    elif source.source == "yfinance":
        if "auto-adjusted" not in header:
            raise _Unavailable("yfinance_adjustment_basis_unverified")
        basis = "yfinance_auto_adjusted_close"
    else:
        raise _Unavailable("market_adjustment_basis_unverified")

    lines = body.splitlines()
    try:
        csv_start = next(
            index for index, line in enumerate(lines) if line.startswith("Date,")
        )
        rows = csv.DictReader(StringIO("\n".join(lines[csv_start:])))
        points, omitted = [], False
        for row in rows:
            session = date.fromisoformat(str(row["Date"]).strip())
            value = float(row.get("Close") or "nan")
            if not calendar.is_trade_date(session):
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
                MarketSeriesPoint(
                    session=session,
                    completed_at=completed_at,
                    adjusted_close=value,
                )
            )
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        raise _Unavailable("market_series_malformed") from exc
    if not points:
        raise _Unavailable("no_admissible_market_rows")
    return (
        MarketSeriesResult(
            instrument=request.instrument,
            source=source.source,
            fallback=source.fallback,
            adjustment_basis=basis,
            retrieved_at=source.retrieved_at,
            points=tuple(points),
        ),
        omitted,
    )


def _source_id(value):
    return re.sub(r"[^a-z0-9_.-]+", "_", value.casefold()).strip("_")


def _pit_origin(source, evidence_type, effective_date):
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        requested=effective_date.isoformat(),
        effective=effective_date.isoformat(),
        effective_date=effective_date,
        timing="mainland market-date filtered",
        retrieved_at=source.retrieved_at.isoformat().replace("+00:00", "Z"),
        fallback=source.fallback,
        temporal_scope="point_in_time",
    )


def _unavailable(domain, code, *, sources=()):
    return CollectionDomainResult(
        domain=domain,
        state=CollectionResultState.UNAVAILABLE,
        sources=tuple(
            source.model_copy(
                update={"diagnostic": CollectionDiagnostic(code=code)}
            )
            for source in sources
        ),
        diagnostic=CollectionDiagnostic(code=code),
    )


def _market_day_end(value):
    return datetime.combine(value, time.max, tzinfo=_SHANGHAI).astimezone(UTC)


def _market_close_at(value):
    if not calendar.is_trade_date(value):
        raise ValueError(f"{value.isoformat()} is not an eligible mainland session")
    return datetime.combine(value, time(15, 30), tzinfo=_SHANGHAI).astimezone(UTC)


def _producer_retrieved_at(value):
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
        raise ValueError("Mainland-China collection clock must include a timezone")
    return value
