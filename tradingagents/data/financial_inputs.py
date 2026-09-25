"""Bounded core financial inputs through the configured public data routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date

from tradingagents.data.context import DataRequestContext
from tradingagents.data.interface import route_to_vendor
from tradingagents.domain.data_result import DataDiagnostic, DataResult
from tradingagents.domain.vendor_errors import VendorRateLimitError


def collect_financial_inputs(
    ticker: str,
    cutoff: str,
    *,
    route: Callable = route_to_vendor,
    include_overview: bool = True,
    stop_on_rate_limit: bool = False,
    data_context: DataRequestContext,
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
        try:
            result = route(
                method, *args, data_context=data_context, _stop_on_rate_limit=stop_on_rate_limit
            )
        except Exception as exc:
            result = DataResult(
                f"<{method} unavailable: {type(exc).__name__}>",
                diagnostics=(DataDiagnostic("source_unavailable", method, type(exc).__name__),),
            )
            responses[method] = result.dump()
            if isinstance(exc, VendorRateLimitError) and stop_on_rate_limit:
                break
        else:
            responses[method] = result.dump()
            observations.extend(result.observations)
    # Bind comparisons to the latest visible release as context, rather than
    # dropping them at Incremental admission or treating each as a new release.
    grouped = {}
    for observation in observations:
        grouped.setdefault((observation.source, observation.kind, observation.is_pit), []).append(
            observation
        )
    compact = []
    for rows in grouped.values():
        if rows[0].kind.startswith("financial_") and len(rows) > 1:
            latest = max(
                rows, key=lambda o: (o.available_on or date.min, o.effective_date or date.min)
            )
            ordered = sorted(
                rows, key=lambda o: (o.effective_date or date.min, o.key), reverse=True
            )
            compact.append(
                replace(
                    latest,
                    values={
                        "periods": [
                            {
                                "report_period": str(o.effective_date)
                                if o.effective_date
                                else None,
                                "disclosed_or_updated_on": str(o.available_on)
                                if o.available_on
                                else None,
                                "values": o.values,
                            }
                            for o in ordered
                        ]
                    },
                )
            )
        else:
            compact.extend(rows)
    unique = {item.identity: item for item in compact}
    return {
        "ticker": ticker,
        "cutoff": cutoff,
        "responses": responses,
        "observations": [item.dump() for item in unique.values()],
    }
