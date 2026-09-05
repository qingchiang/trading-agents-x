"""United States normalization over the established routed dataflows.

This module deliberately owns no provider registry, retry policy, or transport
receipt format.  It calls the same configured router and StockTwits fetcher as
the existing analyst path, then reduces their actual responses to the shared
Incremental collection contract.
"""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from io import StringIO
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from tradingagents.application.contracts import (
    BenchmarkSeriesResult,
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
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope
from tradingagents.dataflows.stocktwits import (
    fetch_stocktwits_messages as _default_stocktwits_fetch,
)
from tradingagents.provenance import extract_provenance, strip_provenance_markers

_NEW_YORK = ZoneInfo("America/New_York")
_BENCHMARKS = (("S&P 500", "^GSPC"), ("Nasdaq 100", "^NDX"))
DEFAULT_ROUTE_TO_VENDOR = _default_route_to_vendor
DEFAULT_STOCKTWITS_FETCH = _default_stocktwits_fetch
_NEWS_ENTRY = re.compile(
    r"^### \[[^]]+\] (?P<title>.+?) \(source: .*?\)\n"
    r"(?:Published: (?P<published>[^\n]+)\n)?"
    r"(?P<body>.*?)(?=^### \[|\Z)",
    re.MULTILINE | re.DOTALL,
)


class _Unavailable(ValueError):
    """A response cannot truthfully become one shared collection result."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def collect_us_incremental(
    request: IncrementalCollectionRequest,
    *,
    route_to_vendor: Callable[..., object] | None = None,
    fetch_stocktwits_messages: Callable[..., str] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IncrementalCollectionResult:
    """Collect US observations through configured legacy routes exactly once each."""
    if request.market != "united_states":
        raise ValueError("US collection requires a United States request")
    routed = route_to_vendor or DEFAULT_ROUTE_TO_VENDOR
    stocktwits = fetch_stocktwits_messages or DEFAULT_STOCKTWITS_FETCH

    domains: list[CollectionDomainResult] = []
    evidence: list[IncrementalEvidenceCandidate] = []
    stock_series: MarketSeriesResult | None = None
    stock_series_evidence_ref: str | None = None

    for domain in request.enabled_domains:
        if domain == "market":
            result, candidate, stock_series = _collect_market(
                request,
                request.instrument,
                route_to_vendor=routed,
                now=now,
            )
            domains.append(result)
            if candidate is not None:
                evidence.append(candidate)
                stock_series_evidence_ref = candidate.evidence.ref
        elif domain == "news":
            result, candidates = _collect_news(request, route_to_vendor=routed, now=now)
            domains.append(result)
            evidence.extend(candidates)
        elif domain == "fundamentals":
            result, candidate = _collect_fundamentals(
                request,
                route_to_vendor=routed,
                now=now,
            )
            domains.append(result)
            if candidate is not None:
                evidence.append(candidate)
        elif domain == "social":
            result, candidate = _collect_social(
                request,
                fetch_stocktwits_messages=stocktwits,
                now=now,
            )
            domains.append(result)
            if candidate is not None:
                evidence.append(candidate)
        else:  # The request contract keeps this defensive branch unreachable.
            raise ValueError(f"unsupported US collection domain: {domain}")

    benchmarks = ()
    if "market" in request.enabled_domains:
        benchmarks = tuple(
            _collect_benchmark(
                request,
                name,
                symbol,
                route_to_vendor=routed,
                now=now,
            )
            for name, symbol in _BENCHMARKS
        )
    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=tuple(domains),
        ),
        evidence=tuple(evidence),
        stock_series=stock_series,
        stock_series_evidence_ref=stock_series_evidence_ref,
        benchmark_series=benchmarks,
    )


def _collect_market(
    request: IncrementalCollectionRequest,
    instrument: str,
    *,
    route_to_vendor: Callable[..., object],
    now: Callable[[], datetime],
) -> tuple[CollectionDomainResult, IncrementalEvidenceCandidate | None, MarketSeriesResult | None]:
    try:
        response = route_to_vendor(
            "get_stock_data",
            instrument,
            _expanded_market_start(request.baseline_analysis_cutoff).isoformat(),
            request.analysis_cutoff.isoformat(),
            _provenance=True,
            _stop_on_rate_limit=True,
        )
        source, body = _routed_text(response, now=now)
        series, omitted = _market_series(request, instrument, source, body)
        current_points = tuple(
            point
            for point in series.points
            if request.window_start < point.completed_at <= request.window_end
        )
        if not current_points:
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
        end_point = current_points[-1]
        item = EvidenceItem.create(
            source=source.source,
            evidence_type="adjusted_close",
            requested_date=request.analysis_cutoff,
            effective_date=end_point.session,
            available_at=end_point.completed_at,
            value=end_point.adjusted_close,
            unit="currency",
            content=(
                f"Provider-adjusted close for {instrument} on {end_point.session.isoformat()}."
            ),
            fallback=source.fallback,
            origins=(_pit_origin(source, "adjusted_close", end_point.session),),
            provenance={"adjustment_basis": series.adjustment_basis},
        )
        state = CollectionResultState.PARTIAL if omitted else CollectionResultState.DATA
        return (
            CollectionDomainResult(
                domain="market",
                state=state,
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
        return _unavailable_domain("market", exc.code), None, None
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable_domain("market", "market_route_failure"), None, None


def _collect_benchmark(
    request: IncrementalCollectionRequest,
    name: str,
    symbol: str,
    *,
    route_to_vendor: Callable[..., object],
    now: Callable[[], datetime],
) -> BenchmarkSeriesResult:
    try:
        response = route_to_vendor(
            "get_stock_data",
            symbol,
            _expanded_market_start(request.baseline_analysis_cutoff).isoformat(),
            request.analysis_cutoff.isoformat(),
            _provenance=True,
            _stop_on_rate_limit=True,
        )
        source, body = _routed_text(response, now=now)
        series, _omitted = _market_series(request, symbol, source, body)
        return BenchmarkSeriesResult(name=name, series=series)
    except _Unavailable as exc:
        return BenchmarkSeriesResult(
            name=name,
            unavailable_diagnostic=CollectionDiagnostic(code=exc.code),
        )
    except VendorRateLimitError:
        raise
    except Exception:
        return BenchmarkSeriesResult(
            name=name,
            unavailable_diagnostic=CollectionDiagnostic(code="benchmark_route_failure"),
        )


def _collect_news(
    request: IncrementalCollectionRequest,
    *,
    route_to_vendor: Callable[..., object],
    now: Callable[[], datetime],
) -> tuple[CollectionDomainResult, tuple[IncrementalEvidenceCandidate, ...]]:
    try:
        response = route_to_vendor(
            "get_news",
            request.instrument,
            request.baseline_analysis_cutoff.isoformat(),
            request.analysis_cutoff.isoformat(),
            _provenance=True,
            _stop_on_rate_limit=True,
        )
        source, body = _routed_text(response, now=now)
        if _is_failure_response(body):
            return _unavailable_domain("news", "news_retrieval_failed", source=source), ()
        if _is_empty_response(body):
            return _bounded_empty("news", source), ()
        candidates = []
        observed = []
        for match in _NEWS_ENTRY.finditer(body):
            published = match.group("published")
            if published is None:
                continue
            available_at, published_on = _parse_news_publication(published)
            if available_at is not None:
                if not request.window_start < available_at <= request.window_end:
                    continue
            elif (
                not request.baseline_analysis_cutoff < published_on <= request.analysis_cutoff
                or (
                    published_on == request.analysis_cutoff
                    and _market_day_end(published_on) > request.window_end
                )
            ):
                continue
            content = f"{match.group('title').strip()}\n{match.group('body').strip()}".strip()
            item = EvidenceItem.create(
                source=source.source,
                evidence_type="news_article",
                requested_date=request.analysis_cutoff,
                effective_date=published_on,
                available_at=available_at,
                content=content,
                fallback=source.fallback,
                origins=(_pit_origin(source, "news_article", published_on),),
            )
            candidates.append(
                IncrementalEvidenceCandidate(
                    evidence=item,
                    available_on=published_on if available_at is None else None,
                )
            )
            observed.append(available_at or _market_day_end(published_on))
        if not candidates:
            return (
                CollectionDomainResult(
                    domain="news",
                    state=CollectionResultState.EMPTY,
                    sources=(source,),
                    diagnostic=CollectionDiagnostic(code="no_reliably_dated_news_records"),
                ),
                (),
            )
        return (
            CollectionDomainResult(
                domain="news",
                state=CollectionResultState.PARTIAL,
                sources=(source,),
                observed_from=min(observed),
                observed_through=max(observed),
                temporal_bases=(CollectionTemporalBasis.PIT,),
                evidence_refs=tuple(candidate.evidence.ref for candidate in candidates),
                diagnostic=CollectionDiagnostic(code="bounded_news_feed"),
            ),
            tuple(candidates),
        )
    except _Unavailable as exc:
        return _unavailable_domain("news", exc.code), ()
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable_domain("news", "news_route_failure"), ()


def _collect_fundamentals(
    request: IncrementalCollectionRequest,
    *,
    route_to_vendor: Callable[..., object],
    now: Callable[[], datetime],
) -> tuple[CollectionDomainResult, IncrementalEvidenceCandidate | None]:
    try:
        response = route_to_vendor(
            "get_fundamentals",
            request.instrument,
            request.analysis_cutoff.isoformat(),
            _provenance=True,
            _stop_on_rate_limit=True,
        )
        source, body = _routed_text(response, now=now)
        if _is_empty_response(body):
            return _bounded_empty("fundamentals", source), None
        if "live" not in body.casefold() and "not point-in-time" not in body.casefold():
            raise _Unavailable("unsupported_fundamentals_temporal_basis")
        item = EvidenceItem.create(
            source=source.source,
            evidence_type="fundamentals_snapshot",
            requested_date=request.analysis_cutoff,
            content=body,
            fallback=source.fallback,
            origins=(_near_live_origin(source, "fundamentals_snapshot"),),
        )
        return (
            CollectionDomainResult(
                domain="fundamentals",
                state=CollectionResultState.PARTIAL,
                sources=(source,),
                temporal_bases=(CollectionTemporalBasis.NEAR_LIVE_ADVISORY,),
                evidence_refs=(item.ref,),
                diagnostic=CollectionDiagnostic(code="near_live_snapshot"),
            ),
            IncrementalEvidenceCandidate(evidence=item),
        )
    except _Unavailable as exc:
        return _unavailable_domain("fundamentals", exc.code), None
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable_domain("fundamentals", "fundamentals_route_failure"), None


def _collect_social(
    request: IncrementalCollectionRequest,
    *,
    fetch_stocktwits_messages: Callable[..., str],
    now: Callable[[], datetime],
) -> tuple[CollectionDomainResult, IncrementalEvidenceCandidate | None]:
    retrieved_at = _aware_now(now)
    try:
        with stop_on_rate_limit_scope(True):
            body = fetch_stocktwits_messages(
                request.instrument,
                limit=30,
                start_date=_social_start_date(request).isoformat(),
                end_date=request.window_end.astimezone(_NEW_YORK).date().isoformat(),
            )
    except VendorRateLimitError:
        raise
    except Exception:
        return _unavailable_domain("social", "stocktwits_transport_failure"), None
    source = CollectionSourceProvenance(source="stocktwits", retrieved_at=retrieved_at)
    if not isinstance(body, str) or _is_empty_response(body):
        return _bounded_empty("social", source), None
    if body.lstrip().startswith("<stocktwits unavailable"):
        return _unavailable_domain("social", "stocktwits_unavailable", source=source), None
    item = EvidenceItem.create(
        source="stocktwits",
        evidence_type="social_snapshot",
        requested_date=request.analysis_cutoff,
        content=body,
        origins=(_near_live_origin(source, "social_snapshot"),),
    )
    observed = []
    for stamp in re.findall(r"^\[([^·]+) ·", body, re.MULTILINE):
        try:
            if stamp.endswith((" EDT", " EST")):
                offset = "-04:00" if stamp.endswith(" EDT") else "-05:00"
                value = datetime.fromisoformat(stamp[:-4] + offset)
            else:
                value = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
            if value.tzinfo is not None:
                observed.append(value)
        except ValueError:
            continue
    return (
        CollectionDomainResult(
            domain="social",
            state=CollectionResultState.PARTIAL,
            sources=(source,),
            observed_from=min(observed) if observed else None,
            observed_through=max(observed) if observed else None,
            temporal_bases=(CollectionTemporalBasis.NEAR_LIVE_ADVISORY,),
            evidence_refs=(item.ref,),
            diagnostic=CollectionDiagnostic(code="bounded_current_social_feed"),
        ),
        IncrementalEvidenceCandidate(evidence=item),
    )


def _routed_text(
    response: object,
    *,
    now: Callable[[], datetime],
) -> tuple[CollectionSourceProvenance, str]:
    if not isinstance(response, str):
        raise _Unavailable("non_text_routed_response")
    if response.startswith(("NO_DATA_AVAILABLE:", "DATA_UNAVAILABLE:", "LIVE_DATA_UNAVAILABLE:")):
        raise _Unavailable("routed_source_unavailable")
    records = extract_provenance(response)
    if len(records) != 1:
        raise _Unavailable("missing_actual_source_provenance")
    record = records[0]
    retrieved_at = _parse_retrieved_at(record.retrieved_at) if record.retrieved_at else _aware_now(now)
    return (
        CollectionSourceProvenance(
            source=record.source,
            fallback="fallback vendor selected" in record.timing.casefold(),
            retrieved_at=retrieved_at,
        ),
        strip_provenance_markers(response).strip(),
    )


def _market_series(
    request: IncrementalCollectionRequest,
    instrument: str,
    source: CollectionSourceProvenance,
    body: str,
) -> tuple[MarketSeriesResult, bool]:
    header = body.casefold()
    match = re.search(r"^# Stock data for (?P<instrument>.+?) from ", body, re.MULTILINE)
    if match is None or match.group("instrument").strip().casefold() != instrument.casefold():
        raise _Unavailable("market_instrument_mismatch")
    if "auto-adjusted" not in header or "yfinance" not in header:
        raise _Unavailable("market_adjustment_basis_unverified")
    lines = body.splitlines()
    try:
        csv_start = next(index for index, line in enumerate(lines) if line.startswith("Date,"))
    except StopIteration as exc:
        raise _Unavailable("market_series_malformed") from exc
    points = []
    omitted = False
    try:
        for row in csv.DictReader(StringIO("\n".join(lines[csv_start:]))):
            session = date.fromisoformat(str(row["Date"]).strip())
            raw_value = row.get("Close") or row.get("Adj Close")
            value = float(raw_value) if raw_value is not None else math.nan
            if (
                not math.isfinite(value)
                or value <= 0
                or not _is_nyse_session(session)
            ):
                omitted = True
                continue
            completed_at = _market_close_at(session)
            if session > request.analysis_cutoff or completed_at > request.window_end:
                omitted = True
                continue
            points.append(
                MarketSeriesPoint(
                    session=session,
                    completed_at=completed_at,
                    adjusted_close=value,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise _Unavailable("market_series_malformed") from exc
    if not points:
        raise _Unavailable("no_admissible_market_rows")
    try:
        return (
            MarketSeriesResult(
                instrument=instrument,
                source=source.source,
                fallback=source.fallback,
                adjustment_basis="yfinance_auto_adjusted_close",
                retrieved_at=source.retrieved_at,
                points=tuple(points),
            ),
            omitted,
        )
    except ValueError as exc:
        raise _Unavailable("market_series_malformed") from exc


def _pit_origin(
    source: CollectionSourceProvenance,
    evidence_type: str,
    effective_date: date,
) -> EvidenceOrigin:
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


def _near_live_origin(
    source: CollectionSourceProvenance,
    evidence_type: str,
) -> EvidenceOrigin:
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        timing="live retrieval-time snapshot",
        retrieved_at=source.retrieved_at.isoformat().replace("+00:00", "Z"),
        fallback=source.fallback,
        temporal_scope="live_only",
    )


def _bounded_empty(domain: str, source: CollectionSourceProvenance) -> CollectionDomainResult:
    return CollectionDomainResult(
        domain=domain,  # type: ignore[arg-type]
        state=CollectionResultState.EMPTY,
        sources=(source,),
        diagnostic=CollectionDiagnostic(code="bounded_feed_no_observed_records"),
    )


def _unavailable_domain(
    domain: str,
    code: str,
    *,
    source: CollectionSourceProvenance | None = None,
) -> CollectionDomainResult:
    return CollectionDomainResult(
        domain=domain,  # type: ignore[arg-type]
        state=CollectionResultState.UNAVAILABLE,
        sources=(source,) if source is not None else (),
        diagnostic=CollectionDiagnostic(code=code),
    )


def _is_empty_response(body: str) -> bool:
    lowered = body.strip().casefold()
    return not lowered or lowered.startswith(("no ", "<no stocktwits messages"))


def _is_failure_response(body: str) -> bool:
    """Recognize adapter failure prose before it can masquerade as an empty feed."""
    lowered = body.strip().casefold()
    return lowered.startswith((
        "error fetching news",
        "error retrieving news",
        "error getting news",
    ))


def _parse_news_publication(value: str) -> tuple[datetime | None, date]:
    """Keep provider timestamps exact; date-only fixtures remain conservative."""
    rendered = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", rendered):
        return None, date.fromisoformat(rendered)
    try:
        published = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Unavailable("invalid_news_publication_time") from exc
    if published.tzinfo is None or published.utcoffset() is None:
        raise _Unavailable("invalid_news_publication_time")
    published = published.astimezone(UTC)
    return published, published.astimezone(_NEW_YORK).date()


def _expanded_market_start(baseline: date) -> date:
    """Fetch enough history to resolve an off-session baseline to its prior close."""
    return baseline - timedelta(days=7)


def _social_start_date(request: IncrementalCollectionRequest) -> date:
    """Start after the sealed baseline market date (the collection window is open)."""
    return request.window_start.astimezone(_NEW_YORK).date() + timedelta(days=1)


@lru_cache(maxsize=1)
def _xnys_calendar():
    """Return the vendored, offline XNYS schedule with ad-hoc closures and early closes."""
    return xcals.get_calendar("XNYS")


def _is_nyse_session(value: date) -> bool:
    """Return whether the authoritative XNYS schedule has a complete session."""
    calendar = _xnys_calendar()
    session = pd.Timestamp(value)
    if session < calendar.first_session or session > calendar.last_session:
        return False
    return calendar.is_session(session)


def _market_day_end(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=_NEW_YORK).astimezone(UTC)


def _market_close_at(value: date) -> datetime:
    """Return XNYS's scheduled close, including early closes, or fail closed."""
    if not _is_nyse_session(value):
        raise ValueError(f"{value.isoformat()} is not an eligible XNYS session")
    close = _xnys_calendar().session_close(pd.Timestamp(value)).to_pydatetime()
    if close.tzinfo is None or close.utcoffset() is None:
        raise ValueError("XNYS schedule returned a naive session close")
    return close.astimezone(UTC)


def _parse_retrieved_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Unavailable("invalid_source_retrieval_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Unavailable("invalid_source_retrieval_time")
    return parsed


def _aware_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("US collection clock must include a timezone")
    return value
