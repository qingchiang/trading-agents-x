"""Bounded core financial inputs through the configured public data routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from tradingagents.provenance import extract_provenance

from .errors import VendorRateLimitError
from .interface import route_to_vendor
from .source_observations import capture_observations


def collect_financial_inputs(
    ticker: str,
    cutoff: str,
    *,
    route: Callable = route_to_vendor,
    include_overview: bool = True,
    stop_on_rate_limit: bool = False,
) -> dict:
    responses = {}
    observations = []
    methods = (["get_fundamentals"] if include_overview else []) + [
        "get_income_statement",
        "get_balance_sheet",
        "get_cashflow",
    ]
    for method in methods:
        args = (ticker, cutoff) if method == "get_fundamentals" else (ticker, "quarterly", cutoff)
        with capture_observations() as captured:
            try:
                responses[method] = str(
                    route(method, *args, _provenance=True, _stop_on_rate_limit=stop_on_rate_limit)
                )
            except VendorRateLimitError:
                if stop_on_rate_limit:
                    raise
                responses[method] = f"<{method} unavailable: VendorRateLimitError>"
            except Exception as exc:
                responses[method] = f"<{method} unavailable: {type(exc).__name__}>"
            else:
                fallback = any(
                    "fallback vendor selected" in record.timing
                    for record in extract_provenance(responses[method])
                )
                observations.extend(replace(o, fallback=o.fallback or fallback) for o in captured)
    unique = {item.identity: item for item in observations}
    return {
        "ticker": ticker,
        "cutoff": cutoff,
        "responses": responses,
        "observations": [item.dump() for item in unique.values()],
    }
