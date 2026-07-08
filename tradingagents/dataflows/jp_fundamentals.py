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
  disclosed with the report and therefore date-safe, shown in every mode. The
  analyst-consensus forward (yfinance ``.info``, a live snapshot) is added as a
  separate **live-only** line, gated by ``_is_live`` so a backtest never sees it.
- Beta → trailing 3-year WEEKLY regression of the stock's returns on TOPIX's
  (the cap-weighted market portfolio, per Japanese valuation practice), both
  J-Quants closes filtered to ``<= curr_date`` (date-safe).
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta

from . import jquants_fundamentals as jqf
from .jquants_common import parse_number as _num
from .jquants_stock import _fetch_ohlcv_frame, fetch_topix_closes
from .y_finance import get_analyst_forward

logger = logging.getLogger(__name__)

# The company-guidance forward (jquants NxFEPS) is date-safe and always shown.
# The analyst-consensus forward (yfinance .info) is a LIVE snapshot with no as-of
# history, so it is emitted ONLY when curr_date is within this many days of today
# (a live/near-live run); a backtest date is always far from today, so it stays
# hidden there — keeping backtests look-ahead safe. See tmp/…plan.md §3.3.
_LIVE_FORECAST_MAX_AGE_DAYS = 5

# One J-Quants OHLCV fetch (trailing 3 years) backs everything price-derived: the
# latest price, the 52-week high/low (sliced from the last year), and the beta
# regression (which needs the full window).
_HISTORY_WINDOW_DAYS = 3 * 365 + 30
_PRICE_WINDOW_DAYS = 365

# Beta follows Japanese valuation practice: a trailing-3-year WEEKLY regression
# against TOPIX (the cap-weighted market portfolio, not the price-weighted Nikkei
# 225). This intentionally differs from benchmark_map[".T"] = "^N225", which the
# reflection layer uses for the *alpha* headline — beta wants the broad market
# portfolio, alpha wants the recognizable index. Require ~1 year of weekly
# returns before reporting it — below that the regression is too noisy (e.g. a
# recent IPO), so it degrades to N/A.
_MARKET_INDEX = "TOPIX"
_BETA_MIN_OBS = 52  # ~1yr of weekly returns


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


def _growth(future_eps: float | None, ttm_eps: float | None) -> float | None:
    """YoY growth of a forward EPS over trailing EPS, or None on a missing /
    non-positive base (keeps PEG and the company-vs-analyst comparison
    well-defined, and keeps both growth figures on identical math)."""
    if future_eps is None or not _pos(ttm_eps):
        return None
    return (future_eps - ttm_eps) / ttm_eps


def _sign(value: float) -> int:
    """-1 / 0 / +1 — so a flat forecast (0) is its own direction, not lumped
    with a decline when comparing company vs analyst growth."""
    return (value > 0) - (value < 0)


def _is_live(curr_date: str) -> bool:
    """True when ``curr_date`` is within ``_LIVE_FORECAST_MAX_AGE_DAYS`` of today.

    Gate for the live-only analyst forward (see the module constant). Uses the
    wall clock deliberately: the live overlay is not meant to be reproducible,
    while a backtest date is always far from today, so backtests stay
    deterministic and look-ahead safe. A malformed date is treated as not-live.
    """
    try:
        age = (datetime.now() - datetime.strptime(curr_date, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return False
    # abs(): within N days EITHER side of today counts as live — a small negative
    # age (curr_date resolved in JST while the host clock lags) is still live,
    # while a far-future date is correctly rejected.
    return abs(age) <= _LIVE_FORECAST_MAX_AGE_DAYS


def _analyst_forward_line(
    ticker: str, price: float | None, ttm_eps: float | None,
    company_growth: float | None, curr_date: str,
) -> str | None:
    """Live-only analyst-consensus forward line, or None in backtest / when absent.

    Only rendered on a (near-)live run (``_is_live``): yfinance's ``.info`` forward
    is a live snapshot that would leak the future in a backtest. Forward PE is
    computed from our own as-of price for single-price consistency; the note
    contrasts the company guidance vs the street to surface a divergence.
    """
    if not _is_live(curr_date):
        return None
    eps, n_analysts = get_analyst_forward(ticker)
    eps = _num(eps)
    if eps is None:
        return None
    fwd_pe = _div(price, _pos(eps))
    analyst_growth = _growth(eps, ttm_eps)
    count = f", {int(n_analysts)} analysts" if n_analysts else ""
    note = ""
    if company_growth is not None and analyst_growth is not None:
        agree = "aligned" if _sign(company_growth) == _sign(analyst_growth) else "divergent"
        note = (
            f"; company guidance {company_growth * 100:+.1f}% vs "
            f"analyst {analyst_growth * 100:+.1f}% ({agree})"
        )
    return (
        f"- Forward PE: {_ratio(fwd_pe)} (analyst consensus, live only{count}, "
        f"EPS {_ratio(eps)}){note}"
    )


def _history_frame(ticker: str, curr_date: str):
    """Return the date-safe trailing-3-year J-Quants OHLCV frame, or None.

    One fetch backs the price, the 52-week high/low, and the beta regression. Any
    fetch failure (no coverage, stale, halted) degrades to None rather than
    breaking the whole overview — the official summary must still render.
    """
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=_HISTORY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    try:
        return _fetch_ohlcv_frame(ticker, start, curr_date)
    except Exception as exc:
        logger.warning("JP fundamentals: price fetch failed for %s: %s", ticker, exc)
        return None


def _price_stats(df, curr_date: str) -> tuple[float | None, str, float | None, float | None]:
    """``(close, price_date, wk52_high, wk52_low)`` from the history frame.

    The high/low use only the trailing 52 weeks (sliced from the 3-year frame);
    the price is the latest row. All-None when the frame is missing/empty.
    """
    if df is None or df.empty:
        return None, "", None, None
    last = df.iloc[-1]
    price_date = last["Date"].strftime("%Y-%m-%d") if hasattr(last["Date"], "strftime") else str(last["Date"])
    year = df[df["Date"] >= pd.Timestamp(curr_date) - pd.Timedelta(days=_PRICE_WINDOW_DAYS)]
    return float(last["Close"]), price_date, float(year["High"].max()), float(year["Low"].min())


def _beta(hist, curr_date: str) -> float | None:
    """Trailing 3-year WEEKLY beta of the stock vs TOPIX, date-safe.

    Both series are J-Quants closes (same session, same ``<= curr_date`` filter,
    so no look-ahead and no cross-vendor drift): the stock's from ``hist``, TOPIX
    from its index endpoint. Each is resampled to weekly (Friday) closes and
    aligned; ``beta = Cov(stock, TOPIX) / Var(TOPIX)`` over the weekly returns.
    Weekly-vs-TOPIX follows Japanese valuation practice. Returns None when TOPIX
    is unavailable, the overlap is too short to be stable, or TOPIX has no
    variance.
    """
    if hist is None:
        return None
    # TOPIX over exactly the stock's fetched window — derive the start from the
    # frame itself so the two series can never drift onto different ranges.
    try:
        topix = fetch_topix_closes(hist["Date"].min().strftime("%Y-%m-%d"), curr_date)
    except Exception as exc:  # index unavailable / no coverage
        logger.warning("JP fundamentals: TOPIX fetch failed: %s", exc)
        return None
    # Resample each to weekly (Friday) closes on a continuous grid, THEN take
    # returns and align — so a missing week (halt, thin trading) becomes a NaN
    # return that drops out, rather than collapsing two weeks into one spanning
    # return that would bias the regression.
    stk_w = hist.set_index("Date")["Close"].resample("W-FRI").last()
    idx_w = topix.set_index("Date")["Close"].resample("W-FRI").last()
    returns = pd.concat({"stk": stk_w.pct_change(), "idx": idx_w.pct_change()}, axis=1).dropna()
    if len(returns) < _BETA_MIN_OBS:
        return None
    var_idx = returns["idx"].var()
    if not var_idx or pd.isna(var_idx):  # 0 or NaN variance → beta undefined
        return None
    beta = returns["stk"].cov(returns["idx"]) / var_idx
    return beta if pd.notna(beta) else None  # never emit a NaN/inf beta


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

    hist = _history_frame(ticker, curr_date)
    price, price_date, wk_high, wk_low = _price_stats(hist, curr_date)
    beta = _beta(hist, curr_date)

    # Derived ratios (all date-safe: jquants summary + as-of price). Owners'-basis
    # ROE = EPS/BPS keeps numerator (parent profit) and denominator (owners' equity
    # per share) on the same basis as PE/PB, unlike NP/NetAssets (NetAssets includes
    # non-controlling interests). Non-positive bases are suppressed via _pos.
    market_cap = price * shares if price is not None and shares is not None else None
    pe = _div(price, _pos(ttm_eps))
    pb = _div(price, bps)
    div_yield = _div(div_ann, price)
    fwd_pe = _div(price, _pos(fwd_eps))
    growth = _growth(fwd_eps, ttm_eps)
    peg = _div(fwd_pe, growth * 100) if _pos(growth) else None
    net_margin = _div(ttm_np, ttm_sales)
    roe = _div(ttm_eps, bps)
    roa = _div(ttm_np, ta)

    price_line = f"{_ratio(price)} (as of {price_date})" if price is not None else "N/A"
    growth_note = f", 1yr growth {growth * 100:+.1f}%" if growth is not None else ""
    lines = [
        "",
        f"\n## Valuation (computed from J-Quants summary + price, date-safe as of {curr_date})",
        f"- Price: {price_line}",
        f"- Market cap: {_money(market_cap)}"
        + (f" (shares {shares:,.0f})" if shares is not None else ""),
        f"- PE: {_ratio(pe)} ({flow_basis})    PB: {_ratio(pb)}",
        f"- Dividend yield: {_pct(div_yield)} (DivAnn {_ratio(div_ann)})    Payout: {_pct(payout)}",
        f"- Forward PE: {_ratio(fwd_pe)} (company guidance / 会社予想, EPS {_ratio(fwd_eps)})"
        f"    PEG: {_ratio(peg)} (company-guidance{growth_note})",
        # Live-only analyst-consensus forward, right below the (date-safe) company
        # guidance; None → dropped by the join filter in a backtest / when absent.
        _analyst_forward_line(ticker, price, ttm_eps, growth, curr_date),
        f"- Net margin: {_pct(net_margin)} ({flow_basis})    ROE: {_pct(roe)}    ROA: {_pct(roa)}"
        f"    Equity ratio: {_ratio(eqar)}",
        f"- 52-week range: {_ratio(wk_low)} – {_ratio(wk_high)}"
        f"    Beta (vs {_MARKET_INDEX}, 3yr weekly): {_ratio(beta)}",
    ]
    return "\n".join(line for line in lines if line is not None)


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
