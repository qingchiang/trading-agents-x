"""Bounded core financial inputs through the configured public data routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date

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
                responses[method] = f"<{method} unavailable: VendorRateLimitError>"
                if stop_on_rate_limit:
                    break
            except Exception as exc:
                responses[method] = f"<{method} unavailable: {type(exc).__name__}>"
            else:
                fallback = any(
                    "fallback vendor selected" in record.timing
                    for record in extract_provenance(responses[method])
                )
                observations.extend(replace(o, fallback=o.fallback or fallback) for o in captured)
    # Bind comparisons to the latest visible release as context, rather than
    # dropping them at Incremental admission or treating each as a new release.
    grouped = {}
    for observation in observations:
        grouped.setdefault((observation.source, observation.kind, observation.is_pit), []).append(observation)
    compact = []
    for rows in grouped.values():
        if rows[0].kind.startswith("financial_") and len(rows) > 1:
            latest = max(rows, key=lambda o: (o.available_on or date.min, o.effective_date or date.min))
            ordered = sorted(rows, key=lambda o: (o.effective_date or date.min, o.key), reverse=True)
            compact.append(replace(latest, values={"periods": [
                {"report_period": str(o.effective_date) if o.effective_date else None,
                 "disclosed_or_updated_on": str(o.available_on) if o.available_on else None,
                 "values": o.values} for o in ordered
            ]}))
        else:
            compact.extend(rows)
    unique = {item.identity: item for item in compact}
    return {
        "ticker": ticker,
        "cutoff": cutoff,
        "responses": responses,
        "observations": [item.dump() for item in unique.values()],
    }
