"""Source-owned metadata for deterministic adapter reports."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from inspect import signature

from tradingagents.domain.data import ProvenanceRecord


def source_metadata(method: str, vendor: str):
    """Describe one typed adapter result before it enters routing."""

    def decorate(fetch):
        parameters = signature(fetch)

        @wraps(fetch)
        def execute(*args, **kwargs):
            result = fetch(*args, **kwargs)
            bound = parameters.bind(*args, **kwargs)
            bound.apply_defaults()
            values = tuple(value for key, value in bound.arguments.items() if key != "data_context")
            if result.news and not result.observations:
                from tradingagents.data.news_selection import news_observations

                result = replace(
                    result,
                    observations=news_observations(
                        result.news, vendor, str(values[0]), global_news=method == "get_global_news"
                    ),
                )
            if not result.provenance and method != "resolve_instrument_eligibility":
                record = describe_source(
                    method, vendor, values, bound.arguments["data_context"].config, result.content
                )
                if record is not None:
                    result = result.with_provenance(record)
            return result

        return execute

    return decorate


def describe_source(
    method: str,
    vendor: str,
    args: tuple,
    config: dict,
    result: str,
) -> ProvenanceRecord | None:
    """Describe the actual successful router leg without inspecting LLM prose."""
    if method == "get_prediction_markets":
        # The graph wrapper owns its immutable analysis date and retrieval time.
        return None

    requested = "unknown"
    effective = "unknown"
    timing = "source-labelled data"
    retrieved_at = None

    if method == "get_stock_data" and len(args) >= 3:
        requested = f"{args[1]} to {args[2]}"
        returned_dates = re.findall(
            r"(?m)^(\d{4}-\d{2}-\d{2})(?=[ T,])",
            result,
        )
        effective = (
            f"{min(returned_dates)} to {max(returned_dates)}"
            if returned_dates
            else "rows filtered within requested window; actual dates unavailable"
        )
        timing = "market-date filtered"
    elif method == "get_indicators" and len(args) >= 3:
        requested = str(args[2])
        effective = (
            _latest_market_observation_date(result, indicator=True)
            or f"latest trading data <= {args[2]}"
        )
        timing = "market-date filtered"
    elif method == "get_verified_market_snapshot" and len(args) >= 2:
        requested = str(args[1])
        effective = (
            _latest_market_observation_date(result, indicator=False)
            or f"latest trading data <= {args[1]}"
        )
        timing = "market-date filtered"
    elif method == "get_news" and len(args) >= 3:
        requested = f"{args[1]} to {args[2]}"
        effective = requested
        timing = (
            f"publication/disclosure-date filtered; returned_items={result.count(chr(10) + '### ')}"
        )
    elif method == "get_global_news" and args:
        end_date = str(args[0])
        lookback = (
            args[1]
            if len(args) > 1 and args[1] is not None
            else config["global_news_lookback_days"]
        )
        try:
            start_date = (
                datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=int(lookback))
            ).strftime("%Y-%m-%d")
            requested = f"{start_date} to {end_date}"
        except (TypeError, ValueError):
            requested = f"ending {end_date}"
        effective = requested
        timing = f"publication-date filtered; returned_items={result.count(chr(10) + '### ')}"
    elif method == "get_macro_indicators" and len(args) >= 2:
        requested = str(args[1])
        effective = f"observations <= {args[1]}"
        timing = "observation-date filtered"
    elif method == "get_fundamentals" and len(args) >= 2:
        requested = str(args[1])
        effective = f"data available for cutoff {args[1]}"
        if "LIVE_DATA_UNAVAILABLE" in result:
            timing = "unavailable for historical date; vendor not queried"
        elif vendor in {"yfinance", "alpha_vantage"}:
            timing = "live non-point-in-time"
            retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
        else:
            timing = "disclosure-date filtered"
    elif method in {"get_balance_sheet", "get_cashflow", "get_income_statement"}:
        curr_date = args[2] if len(args) >= 3 else None
        requested = str(curr_date or "live retrieval")
        effective = f"fiscal period ends <= {curr_date}" if curr_date else "current statement frame"
        if "LIVE_DATA_UNAVAILABLE" in result:
            timing = "unavailable for historical date; vendor not queried"
        elif vendor in {"yfinance", "alpha_vantage"}:
            timing = "period-end filtered only; not point-in-time"
            retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
        else:
            timing = "disclosure-date filtered"

    lowered = result.casefold()
    if "live_data_unavailable" in lowered:
        effective = "—"
        timing = (
            "live-only; unavailable for historical or future date; vendor not queried"
            if vendor in {"yfinance", "alpha_vantage"}
            and method
            in {
                "get_fundamentals",
                "get_balance_sheet",
                "get_cashflow",
                "get_income_statement",
            }
            else "unavailable for historical date; vendor not queried"
        )
        retrieved_at = None
    elif (
        "data_unavailable" in lowered
        or "error fetching" in lowered
        or "error retrieving" in lowered
    ):
        effective = "—"
        timing = "retrieval unavailable"
    elif method in {"get_news", "get_global_news"} and (
        lowered.startswith("no ") or "no relevant news" in lowered
    ):
        timing = "available; no relevant items in window"

    return ProvenanceRecord(
        evidence=method,
        source=vendor,
        requested=requested,
        effective=effective,
        timing=timing,
        retrieved_at=retrieved_at,
    )


def _latest_market_observation_date(result: str, *, indicator: bool) -> str | None:
    """Extract an explicit successful observation date without guessing cutoff."""

    labels = (
        ("Latest valid indicator observation", "Effective trading date")
        if indicator
        else ("Latest trading row used", "Effective trading date")
    )
    for label in labels:
        match = re.search(
            rf"(?mi)^[#\- ]*{re.escape(label)}:\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*$",
            result,
        )
        if match:
            return match.group(1)
    if not indicator:
        return None
    dated_values = re.findall(
        r"(?m)^(\d{4}-\d{2}-\d{2}):\s*(?!N/A(?:\b|:))\S.*$",
        result,
    )
    return max(dated_values) if dated_values else None
