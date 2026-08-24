"""Japanese Incremental v1 normalization over configured Tokyo dataflows.

The collector deliberately stays at the existing router/assembler boundary.
It records observations the J-Quants, EDINET/TDnet/news, and fundamentals
paths actually returned; it neither creates a second provider registry nor
turns bounded Japanese archives into a completeness claim.
"""

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
from tradingagents.dataflows.errors import VendorRateLimitError
from tradingagents.dataflows.interface import route_to_vendor as _default_route_to_vendor
from tradingagents.dataflows.jp.calendar import completed_market_date, is_tse_open
from tradingagents.provenance import (
    EvidenceSpan,
    extract_evidence_spans,
    extract_provenance,
    strip_provenance_markers,
    temporal_scope_from_records,
)

_TOKYO = ZoneInfo("Asia/Tokyo")
DEFAULT_ROUTE_TO_VENDOR = _default_route_to_vendor
_NEWS_ITEM = re.compile(r"^### (?P<title>.+?)\n(?P<body>.*?)(?=^### |\Z)", re.MULTILINE | re.DOTALL)
_DISCLOSED_AT = re.compile(
    r"^(?P<label>Submitted|Disclosed|Published):\s*(?P<value>\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2}|\s+JST)?)?)",
    re.MULTILINE,
)
_EFFECTIVE_DATE = re.compile(r"^Effective period:\s*(?P<value>\d{4}-\d{2}-\d{2})", re.MULTILINE)
_DATE_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.MULTILINE)
_DISCLOSURE_DATE = re.compile(r"\bdisclosed\s+(?P<value>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_FUNDAMENTAL_PERIOD_END = re.compile(
    r"\b(?:FY|Q[1-4]) end (?P<value>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE
)


class _Unavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def collect_japan_incremental(
    request: IncrementalCollectionRequest,
    *,
    route_to_vendor: Callable[..., object] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IncrementalCollectionResult:
    """Collect enabled Tokyo domains through their configured routes once each."""
    if request.market != "japan":
        raise ValueError("Japanese collection requires a Japan request")
    routed = route_to_vendor or DEFAULT_ROUTE_TO_VENDOR
    domains: list[CollectionDomainResult] = []
    evidence: list[IncrementalEvidenceCandidate] = []
    stock_series: MarketSeriesResult | None = None
    stock_series_evidence_ref: str | None = None

    for domain in request.enabled_domains:
        if domain == "market":
            result, candidate, stock_series = _collect_market(request, routed, now)
            domains.append(result)
            if candidate is not None:
                evidence.append(candidate)
                stock_series_evidence_ref = candidate.evidence.ref
        elif domain == "news":
            result, candidates = _collect_news(request, routed, now)
            domains.append(result)
            evidence.extend(candidates)
        elif domain == "fundamentals":
            result, candidates = _collect_fundamentals(request, routed, now)
            domains.append(result)
            evidence.extend(candidates)
        elif domain == "social":
            # The existing JP sentiment tools are analyst-only aggregates, not a
            # configured collection route with auditable observation timestamps.
            domains.append(_unavailable("social", "japan_social_route_unavailable"))
        else:
            raise ValueError(f"unsupported Japanese collection domain: {domain}")

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
            _provenance=True,
            _stop_on_rate_limit=True,
            _require_adjusted=True,
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
    except _Unavailable as exc:
        return _unavailable("market", exc.code, sources=(source,) if source else ()), None, None
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
        response = routed(
            "get_news",
            request.instrument,
            request.baseline_analysis_cutoff.isoformat(),
            request.analysis_cutoff.isoformat(),
            _provenance=True,
            _stop_on_rate_limit=True,
        )
        sources, body = _routed_sources(response, now)
        if _is_failure(body):
            return _unavailable("news", "news_retrieval_failed", sources=sources), ()
        records = extract_provenance(response)
        limited_sources = _news_availability_sources(records, now)
        cap_limited_sources = _news_global_cap_sources(records, now)
        if _is_empty(body):
            summary_sources = _merge_sources(sources, limited_sources, cap_limited_sources)
            if limited_sources:
                return (
                    CollectionDomainResult(
                        domain="news",
                        state=CollectionResultState.EMPTY,
                        sources=summary_sources,
                        diagnostic=CollectionDiagnostic(
                            code="bounded_japanese_news_feed_with_upstream_unavailable"
                        ),
                    ),
                    (),
                )
            if cap_limited_sources:
                return (
                    CollectionDomainResult(
                        domain="news",
                        state=CollectionResultState.EMPTY,
                        sources=summary_sources,
                        diagnostic=CollectionDiagnostic(
                            code="bounded_japanese_news_feed_with_global_cap"
                        ),
                    ),
                    (),
                )
            return _bounded_empty("news", sources), ()
        candidates: list[IncrementalEvidenceCandidate] = []
        observed = []
        used_sources: dict[str, CollectionSourceProvenance] = {}
        temporal_limited_sources: dict[str, CollectionSourceProvenance] = {}
        bases: list[CollectionTemporalBasis] = []
        for span in _news_spans(response, body):
            if span.records and all(_is_news_availability_record(record) for record in span.records):
                continue
            if span.content is None or len(span.records) != 1:
                if span.content and span.records:
                    raise _Unavailable("unbound_news_item_provenance")
                continue
            source = _sources_from_records(span.records, now)[0]
            if span.temporal_scope == "unknown":
                temporal_limited_sources[source.source] = source.model_copy(
                    update={"diagnostic": CollectionDiagnostic(code="unknown_news_temporal_scope")}
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
                    content = f"{match.group('title').strip()}\n{match.group('body').strip()}".strip()
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
                        update={"diagnostic": CollectionDiagnostic(code="near_live_snapshot")}
                    )
                bases.append(CollectionTemporalBasis.NEAR_LIVE_ADVISORY)
                continue
            origin = _origin_from_record(
                span.records[0], source, "disclosure_or_news", temporal_scope="point_in_time"
            )
            for match in _NEWS_ITEM.finditer(span.content):
                content = f"{match.group('title').strip()}\n{match.group('body').strip()}".strip()
                available_at, available_on = _publication_time(content, source=source)
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
                        available_on=available_on if available_at is None else None,
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
            temporal_code = _news_temporal_limitation_code(temporal_limited_sources.values())
            diagnostic_code = (
                "bounded_japanese_news_feed_with_upstream_unavailable"
                if limited_sources
                else (
                    "bounded_japanese_news_feed_with_global_cap"
                    if cap_limited_sources
                    else (temporal_code or "no_reliably_dated_japanese_records")
                )
            )
            return (
                CollectionDomainResult(
                    domain="news",
                    state=CollectionResultState.EMPTY,
                    sources=summary_sources,
                    diagnostic=CollectionDiagnostic(code=diagnostic_code),
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
                evidence_refs=tuple(candidate.evidence.ref for candidate in candidates),
                diagnostic=CollectionDiagnostic(
                    code=(
                        "bounded_japanese_news_feed_with_upstream_unavailable"
                        if limited_sources
                        else (
                            "bounded_japanese_news_feed_with_global_cap"
                            if cap_limited_sources
                            else "bounded_japanese_news_feed"
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
            return _unavailable("fundamentals", "fundamentals_retrieval_failed", sources=sources), ()
        if _is_empty(body):
            return _bounded_empty("fundamentals", sources), ()
        spans = _fundamentals_spans(response, body)
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
                        _origin_from_record(
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
            disclosed = _fundamentals_disclosure_date(span.content)
            if disclosed is None:
                continue
            available_on = _fundamentals_available_on(span.records, disclosed)
            available_at = _market_day_end(available_on)
            if not request.window_start < available_at <= request.window_end:
                continue
            effective = _fundamentals_effective_date(span.content) or disclosed
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
                    for record, actual_source in zip(span.records, span_sources, strict=True)
                ),
            )
            candidates.append(IncrementalEvidenceCandidate(evidence=item, available_on=available_on))
            reported_sources.update({source.source: source for source in span_sources})
            bases.append(CollectionTemporalBasis.PIT)
        if not candidates:
            summary_sources = _merge_sources(sources, tuple(temporal_limited_sources.values()))
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
                sources=_merge_sources(
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
                            else "mixed_pit_and_near_live_fundamentals"
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
        return _unavailable("fundamentals", "fundamentals_route_failure", sources=sources), ()


def _routed_source(response: object, now):
    sources, body = _routed_sources(response, now)
    if len(sources) != 1:
        raise _Unavailable("ambiguous_market_source_provenance")
    return sources[0], body


def _routed_sources(response: object, now):
    if not isinstance(response, str):
        raise _Unavailable("non_text_routed_response")
    if response.startswith(("NO_DATA_AVAILABLE:", "DATA_UNAVAILABLE:", "LIVE_DATA_UNAVAILABLE:")):
        raise _Unavailable("routed_source_unavailable")
    records = extract_provenance(response)
    if not records:
        raise _Unavailable("missing_actual_source_provenance")
    sources = _sources_from_records(records, now)
    return sources, strip_provenance_markers(response).strip()


def _sources_from_records(records, now):
    return tuple(
        CollectionSourceProvenance(
            source=_source_id(record.source),
            fallback="fallback vendor selected" in record.timing.casefold(),
            retrieved_at=_producer_retrieved_at(record.retrieved_at) or _aware_now(now),
        )
        for record in records
    )


def _market_series(request, source, body):
    match = re.search(r"^# Stock data for (?P<instrument>.+?) from ", body, re.MULTILINE)
    if (
        match is None
        or match.group("instrument").strip().casefold() != request.instrument.casefold()
    ):
        raise _Unavailable("market_instrument_mismatch")
    header = body.casefold()
    if source.source.casefold() == "jquants":
        if "j-quants split/dividend-adjusted close" not in header:
            raise _Unavailable("jquants_adjustment_basis_unverified")
        basis = "jquants_split_dividend_adjusted_close"
    elif source.source.casefold() == "yfinance":
        if "auto-adjusted" not in header:
            raise _Unavailable("yfinance_adjustment_basis_unverified")
        basis = "yfinance_auto_adjusted_close"
    else:
        raise _Unavailable("market_adjustment_basis_unverified")
    lines = body.splitlines()
    try:
        csv_start = next(index for index, line in enumerate(lines) if line.startswith("Date,"))
        rows = csv.DictReader(StringIO("\n".join(lines[csv_start:])))
        points, omitted = [], False
        for row in rows:
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
        raise _Unavailable("market_series_malformed") from exc
    if not points:
        raise _Unavailable("no_admissible_market_rows")
    return MarketSeriesResult(
        instrument=request.instrument,
        source=source.source,
        fallback=source.fallback,
        adjustment_basis=basis,
        retrieved_at=source.retrieved_at,
        points=tuple(points),
    ), omitted


def _publication_time(content, *, source):
    timestamp = _DISCLOSED_AT.search(content)
    if timestamp is not None:
        value = timestamp.group("value")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return None, date.fromisoformat(value)
        available_at = _parse_datetime(
            value,
            allow_naive_tokyo=(
                timestamp.group("label") == "Submitted" and source.source == "edinet"
            ),
        )
        return available_at, available_at.astimezone(_TOKYO).date()
    date_line = _DATE_LINE.search(content)
    return (None, date.fromisoformat(date_line.group(0))) if date_line else (None, None)


def _parse_datetime(value, *, allow_naive_tokyo=False):
    rendered = value.replace("JST", "+09:00").strip()
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Unavailable("invalid_disclosure_publication_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not allow_naive_tokyo:
            raise _Unavailable("invalid_disclosure_publication_time")
        parsed = parsed.replace(tzinfo=_TOKYO)
    return parsed.astimezone(UTC)


def _effective_date(content):
    match = _EFFECTIVE_DATE.search(content)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group("value"))
    except ValueError as exc:
        raise _Unavailable("invalid_disclosure_effective_period") from exc


def _fundamentals_disclosure_date(body):
    match = _DISCLOSURE_DATE.search(body)
    return date.fromisoformat(match.group("value")) if match else None


def _fundamentals_effective_date(body):
    match = _EFFECTIVE_DATE.search(body) or _FUNDAMENTAL_PERIOD_END.search(body)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group("value"))
    except ValueError as exc:
        raise _Unavailable("invalid_fundamentals_effective_period") from exc


def _fundamentals_available_on(records, disclosed):
    observed_dates = [disclosed]
    for record in records:
        observed_dates.extend(_reliable_record_observation_dates(record))
    return max(observed_dates)


def _reliable_record_observation_dates(record):
    """Return dates that identify observed composition, never query bounds."""
    timing = record.timing.casefold()
    effective = record.effective.strip()
    if "market-date filtered" in timing:
        return tuple(
            date.fromisoformat(value)
            for value in re.findall(r"\d{4}-\d{2}-\d{2}", effective)
        )
    if "publication" in timing or "disclosure-date" in timing:
        try:
            return (date.fromisoformat(effective),)
        except ValueError:
            return ()
    return ()


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


def _news_spans(response, body):
    spans = extract_evidence_spans(response)
    if spans:
        return tuple(spans)
    records = tuple(extract_provenance(response))
    selected_fallback = tuple(
        record for record in records if "fallback vendor selected" in record.timing.casefold()
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


def _news_availability_sources(records, now):
    unavailable = {}
    for record, source in zip(records, _sources_from_records(records, now), strict=True):
        if not _is_news_availability_record(record):
            continue
        unavailable[source.source] = source.model_copy(
            update={"diagnostic": CollectionDiagnostic(code="upstream_source_unavailable")}
        )
    return tuple(unavailable.values())


def _news_global_cap_sources(records, now):
    omitted = {}
    for record, source in zip(records, _sources_from_records(records, now), strict=True):
        if not _is_news_global_cap_record(record):
            continue
        omitted[source.source] = source.model_copy(
            update={"diagnostic": CollectionDiagnostic(code="truncated_by_global_cap")}
        )
    return tuple(omitted.values())


def _merge_sources(*source_groups):
    merged = {}
    for group in source_groups:
        for source in group:
            merged[source.source] = source
    return tuple(merged.values())


def _news_temporal_limitation_code(sources):
    diagnostics = {source.diagnostic.code for source in sources if source.diagnostic}
    if diagnostics == {"unknown_news_temporal_scope"}:
        return "unknown_news_temporal_scope"
    if diagnostics == {"unreliable_live_news_retrieval_time"}:
        return "unreliable_live_news_retrieval_time"
    return "news_temporal_scope_unavailable" if diagnostics else None


def _fundamentals_temporal_limitation_code(sources):
    diagnostics = {source.diagnostic.code for source in sources if source.diagnostic}
    if diagnostics == {"unknown_fundamentals_temporal_scope"}:
        return "unknown_fundamentals_temporal_scope"
    if diagnostics == {"unreliable_live_fundamentals_retrieval_time"}:
        return "unreliable_live_fundamentals_retrieval_time"
    return "fundamentals_temporal_scope_unavailable" if diagnostics else None


def _is_news_availability_record(record):
    timing = record.timing.casefold()
    return "fallback vendor selected" not in timing and "unavailable" in timing


def _is_news_global_cap_record(record):
    timing = record.timing.casefold()
    return "truncated_by_global_cap=" in timing and "kept_items=0" in timing


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


def _near_live_origin(source, evidence_type):
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        timing="live retrieval-time snapshot",
        retrieved_at=source.retrieved_at.isoformat().replace("+00:00", "Z"),
        fallback=source.fallback,
        temporal_scope="live_only",
    )


def _origin_from_record(record, source, evidence_type, *, temporal_scope):
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        requested=record.requested or "unknown",
        effective=record.effective or "unknown",
        effective_date=_origin_effective_date(record.effective),
        timing=record.timing or "unknown",
        retrieved_at=record.retrieved_at or source.retrieved_at.isoformat().replace("+00:00", "Z"),
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


def _unavailable(domain, code, *, sources=()):
    return CollectionDomainResult(
        domain=domain,
        state=CollectionResultState.UNAVAILABLE,
        sources=tuple(
            source.model_copy(update={"diagnostic": CollectionDiagnostic(code=code)})
            for source in sources
        ),
        diagnostic=CollectionDiagnostic(code=code),
    )


def _is_empty(body):
    lowered = body.strip().casefold()
    return not lowered or lowered.startswith("no ")


def _is_failure(body):
    return (
        body.strip().casefold().startswith(("error fetching", "error retrieving", "error getting"))
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
