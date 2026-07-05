"""JP fundamentals assembler: J-Quants official summary + date-safe valuation ratios.

J-Quants' ``/fins/summary`` gives official absolute figures (sales, profit, EPS,
BPS, assets, cash flow) but no valuation/market ratios (PE, PB, market cap,
dividend yield, forward metrics). yfinance *has* those for ``.T`` names, but its
``.info`` is a live snapshot — injecting today's PE into a historical backtest
leaks the future. So for Japanese tickers we compute the ratios ourselves from
the J-Quants summary plus the as-of-``curr_date`` price, which is fully
look-ahead safe (every input is filtered to ``<= curr_date``) and single-source
(no cross-vendor basis mixing).

This backs ``get_fundamentals`` for ``.T`` (registered ahead of ``jquants`` in the
chain); the three statement tools stay on ``jquants`` (its summary is the freshest
official filing). See ``tmp/jp_fundamentals_assembler_plan.md`` for the design.

Basis conventions (labelled in the output so nothing is silently cross-compared):
- Flow items (sales, net profit, EPS) → **TTM**, rolled from the cumulative
  (YTD) quarterly disclosures; degrades to the latest full FY when the rolling
  inputs aren't all available on/before ``curr_date``.
- Balance items (equity, assets, BPS, shares) → latest full-year point.
- Forward (PE/PEG) → the company's own guidance (会社予想, ``NxFEPS``), which is
  disclosed with the report and therefore date-safe. The analyst-consensus
  forward (yfinance, live) is a separate live-only overlay (Phase 3), not here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from dateutil.relativedelta import relativedelta

from . import jquants_fundamentals as jqf
from .jquants_common import parse_number as _num
from .jquants_stock import _fetch_ohlcv_frame

logger = logging.getLogger(__name__)

# 52-week window (calendar days) fetched for the high/low range and latest price.
_PRICE_WINDOW_DAYS = 365


def _minus_one_year(date_str) -> str | None:
    """Return ``date_str`` (YYYY-MM-DD) shifted back one year, or None when the
    input is missing/malformed (so the TTM roll degrades to FY instead of crashing
    the whole valuation block on a partial feed)."""
    try:
        return (datetime.strptime(date_str, "%Y-%m-%d") - relativedelta(years=1)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _is_statement(r: dict) -> bool:
    """True for a record carrying actual results (not a forecast-only revision)."""
    return _num(r.get("Sales")) is not None or _num(r.get("NP")) is not None


def _find(records, per_type: str, per_end: str | None):
    """First record matching a CurPerType and period-end date (records are newest-first).

    Returns None for a missing ``per_end`` so a malformed/absent date never
    matches a record that also lacks the field.
    """
    if per_end is None:
        return None
    for r in records:
        if r.get("CurPerType") == per_type and r.get("CurPerEn") == per_end:
            return r
    return None


def _ttm(field: str, statements: list[dict]) -> tuple[float | None, str]:
    """Return ``(value, basis)`` for a flow ``field`` on a trailing-12-month basis.

    Japanese quarterly disclosures are cumulative (YTD), so when the latest
    statement is a quarter Q the TTM value is
    ``Q_cumulative + prior_full_FY - prior_year_same_Q_cumulative``. When the
    latest statement is itself a full year, TTM == that FY. Degrades to the latest
    full FY (labelled) when the rolling inputs aren't all present.
    """
    if not statements:
        return None, ""
    latest = statements[0]
    latest_val = _num(latest.get(field))

    if latest.get("CurPerType") == "FY":
        return latest_val, "TTM"

    # Latest is a cumulative quarter → roll: + prior full FY − prior-year same quarter.
    prior_fy = _find(statements, "FY", _minus_one_year(latest.get("CurFYEn")))
    prior_q = _find(statements, latest.get("CurPerType"), _minus_one_year(latest.get("CurPerEn")))
    if prior_fy and prior_q:
        pf, pq = _num(prior_fy.get(field)), _num(prior_q.get(field))
        if None not in (latest_val, pf, pq):
            return latest_val + pf - pq, "TTM"

    # Degrade: use the most recent full FY's annual figure (a real 12-month value,
    # just staler than a rolled TTM). Labelled so the basis is explicit.
    fy = next((r for r in statements if r.get("CurPerType") == "FY"), None)
    if fy is not None:
        return _num(fy.get(field)), "FY (TTM unavailable)"
    return None, ""


def _ttm_flows(statements: list[dict], fy: dict) -> tuple[float | None, float | None, float | None, str]:
    """Return ``(eps, np, sales, basis)`` for the flow trio on ONE shared basis.

    Each of EPS/NP/Sales rolls to TTM independently; if they don't all roll
    cleanly, the whole trio degrades to the latest full FY (``fy``) so a ratio
    like net margin never divides an FY numerator by a rolled-TTM denominator.
    """
    values, bases = {}, set()
    for field in ("EPS", "NP", "Sales"):
        values[field], basis = _ttm(field, statements)
        bases.add(basis)
    if bases == {"TTM"}:
        return values["EPS"], values["NP"], values["Sales"], "TTM"
    return _num(fy.get("EPS")), _num(fy.get("NP")), _num(fy.get("Sales")), "FY (TTM unavailable)"


def _forward_eps(statements: list[dict]) -> float | None:
    """Company-guidance (会社予想) forward EPS.

    Mid-year, the fresh current-FY forecast is the latest statement's ``FEPS``; at
    year-end that field is blank and next-FY guidance sits in ``NxFEPS``. Prefer
    the latest ``FEPS``, else the newest available ``NxFEPS``.
    """
    latest_feps = _num(statements[0].get("FEPS"))
    if latest_feps is not None:
        return latest_feps
    return next((_num(r.get("NxFEPS")) for r in statements if _num(r.get("NxFEPS")) is not None), None)


def _money(value: float | None) -> str:
    """Humanize a JPY amount (¥…T / ¥…B / ¥…M), or N/A."""
    if value is None:
        return "N/A"
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= scale:
            return f"¥{value / scale:.2f}{suffix}"
    return f"¥{value:,.0f}"


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _ratio(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def _div(a: float | None, b: float | None) -> float | None:
    """Safe divide: None if either operand is missing or the denominator is 0."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pos(value: float | None) -> float | None:
    """Return ``value`` only when it is strictly positive, else None.

    Guards the ratios that are meaningless (or misleading) on a non-positive base:
    PE / forward PE on negative EPS, PEG on a forecast decline.
    """
    return value if value is not None and value > 0 else None


def _latest_price(ticker: str, curr_date: str) -> tuple[float | None, str, float | None, float | None]:
    """Return ``(close, price_date, wk52_high, wk52_low)`` as of ``curr_date``.

    Reuses the date-safe J-Quants OHLCV frame (rows only within the window). Any
    fetch failure (no coverage, stale, halted) degrades to all-None rather than
    breaking the whole overview — the official summary must still render.
    """
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=_PRICE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    try:
        df = _fetch_ohlcv_frame(ticker, start, curr_date)
    except Exception as exc:
        logger.warning("JP fundamentals: price fetch failed for %s: %s", ticker, exc)
        return None, "", None, None
    last = df.iloc[-1]
    price_date = last["Date"].strftime("%Y-%m-%d") if hasattr(last["Date"], "strftime") else str(last["Date"])
    return float(last["Close"]), price_date, float(df["High"].max()), float(df["Low"].min())


def _valuation_block(ticker: str, curr_date: str) -> str:
    """Render the computed, date-safe valuation block for ``ticker`` as of ``curr_date``."""
    _canonical, records = jqf.fetch_periods(ticker, curr_date)
    statements = [r for r in records if _is_statement(r)]
    if not statements:
        return "\n\n## Valuation (computed)\n(unavailable: no statement disclosures)"

    latest_fy = next((r for r in statements if r.get("CurPerType") == "FY"), None)
    fy = latest_fy or {}  # balance-point fields live on the full-year filing

    # Flow trio (EPS/NP/Sales) on one shared basis; balance-point fields from the
    # latest full year; forward EPS from company guidance.
    ttm_eps, ttm_np, ttm_sales, flow_basis = _ttm_flows(statements, fy)
    bps = _num(fy.get("BPS"))
    ta = _num(fy.get("TA"))
    shout, treasury = _num(fy.get("ShOutFY")), _num(fy.get("TrShFY"))
    # Treasury shares net out of the float; if the treasury field is absent the
    # gross count is a close (≈0.4%) approximation, so fall back to it.
    shares = shout - treasury if shout is not None and treasury is not None else shout
    div_ann = _num(fy.get("DivAnn"))
    payout = _num(fy.get("PayoutRatioAnn"))
    eqar = _num(fy.get("EqAR"))
    fwd_eps = _forward_eps(statements)

    price, price_date, wk_high, wk_low = _latest_price(ticker, curr_date)

    # Derived ratios (all date-safe: jquants summary + as-of price). Owners'-basis
    # ROE = EPS/BPS keeps numerator (parent profit) and denominator (owners' equity
    # per share) on the same basis as PE/PB, unlike NP/NetAssets (NetAssets includes
    # non-controlling interests). Non-positive bases are suppressed via _pos.
    market_cap = price * shares if price is not None and shares is not None else None
    pe = _div(price, _pos(ttm_eps))
    pb = _div(price, bps)
    div_yield = _div(div_ann, price)
    fwd_pe = _div(price, _pos(fwd_eps))
    growth = _div(fwd_eps - ttm_eps, ttm_eps) if fwd_eps is not None and _pos(ttm_eps) else None
    peg = _div(fwd_pe, growth * 100) if _pos(growth) else None
    net_margin = _div(ttm_np, ttm_sales)
    roe = _div(ttm_eps, bps)
    roa = _div(ttm_np, ta)

    price_line = f"{_ratio(price)} (as of {price_date})" if price is not None else "N/A"
    growth_note = f", 1yr growth {growth * 100:+.1f}%" if growth is not None else ""
    return "\n".join([
        "",
        f"\n## Valuation (computed from J-Quants summary + price, date-safe as of {curr_date})",
        f"- Price: {price_line}",
        f"- Market cap: {_money(market_cap)}"
        + (f" (shares {shares:,.0f})" if shares is not None else ""),
        f"- PE: {_ratio(pe)} ({flow_basis})    PB: {_ratio(pb)}",
        f"- Dividend yield: {_pct(div_yield)} (DivAnn {_ratio(div_ann)})    Payout: {_pct(payout)}",
        f"- Forward PE: {_ratio(fwd_pe)} (company guidance / 会社予想, EPS {_ratio(fwd_eps)})"
        f"    PEG: {_ratio(peg)} (company-guidance{growth_note})",
        f"- Net margin: {_pct(net_margin)} ({flow_basis})    ROE: {_pct(roe)}    ROA: {_pct(roa)}"
        f"    Equity ratio: {_ratio(eqar)}",
        f"- 52-week range: {_ratio(wk_low)} – {_ratio(wk_high)}",
    ])


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Official J-Quants overview plus a date-safe computed valuation block.

    The base overview comes from :func:`jquants_fundamentals.get_fundamentals`
    (raises ``NoMarketDataError`` when nothing is disclosed on/before
    ``curr_date`` — letting the router fall through to another vendor). The
    valuation block is best-effort: any failure degrades to a short note rather
    than losing the official summary.
    """
    base = jqf.get_fundamentals(ticker, curr_date)
    as_of = curr_date or datetime.now().strftime("%Y-%m-%d")
    try:
        return base + _valuation_block(ticker, as_of)
    except Exception as exc:  # never let ratio math break the official overview
        logger.warning("JP fundamentals: valuation block failed for %s: %s", ticker, exc)
        return base + "\n\n## Valuation (computed)\n(unavailable: ratio computation failed)"
