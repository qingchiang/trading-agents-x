"""Producer-owned observations shared by Full and Incremental research.

The scoped sink transports structured values beside existing string interfaces;
it never parses rendered reports or changes provider routing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents.domain.data import SourceObservation, as_date, scalar


def make_observation(
    source: str,
    kind: str,
    key: str,
    values: dict,
    *,
    effective_date=None,
    available_on=None,
    available_at=None,
    retrieved_at: datetime | None = None,
    timing: str | None = None,
    fallback: bool = False,
) -> SourceObservation:
    return SourceObservation(
        source=source,
        kind=kind,
        key=str(key),
        values=scalar(values),
        effective_date=as_date(effective_date),
        available_on=as_date(available_on),
        available_at=available_at,
        retrieved_at=retrieved_at or datetime.now(UTC),
        timing=timing
        or (
            "publication-date filtered"
            if available_on or available_at
            else "near-live snapshot; publication time unavailable"
        ),
        fallback=fallback,
    )


_FINANCIAL_FIELDS = {
    "income": ("Total Revenue", "Operating Income", "Net Income", "Basic EPS"),
    "balance": (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Total Debt",
        "Total Assets",
        "Total Liabilities Net Minority Interest",
        "Stockholders Equity",
    ),
    "cashflow": (
        "Operating Cash Flow",
        "Capital Expenditure",
        "Free Cash Flow",
        "Investing Cash Flow",
        "Financing Cash Flow",
        "End Cash Position",
    ),
}


def yahoo_statement_observations(frame, ticker: str, kind: str, freq: str, *, source="yfinance"):
    from tradingagents.domain.measurement import instrument_currency

    observations = []
    for period in sorted(frame.columns, reverse=True)[:4]:
        values = {
            label: scalar(frame.loc[label, period])
            for label in _FINANCIAL_FIELDS[kind]
            if label in frame.index
        }
        if not any(value is not None for value in values.values()):
            continue
        values.update(
            currency=instrument_currency(ticker),
            currency_basis="instrument market convention; statement currency unverified",
            unit="currency units; EPS per share",
            frequency=freq,
            period_basis="provider fiscal period; not a filing timestamp",
        )
        observations.append(
            make_observation(
                source,
                f"financial_{kind}",
                f"{ticker}:{as_date(period)}",
                values,
                effective_date=period,
            )
        )
    return tuple(observations)
