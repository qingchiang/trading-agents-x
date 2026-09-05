from datetime import datetime
from typing import Annotated

import yfinance as yf
from dateutil.relativedelta import relativedelta

from .errors import VendorRateLimitError
from .lookahead import is_near_live
from .macro_common import SeriesCache
from .rate_limit import stop_on_rate_limit_requested
from .stockstats_utils import (
    INDICATOR_DESCRIPTIONS,
    StockstatsUtils,
    _assert_ohlcv_not_stale,
    _coerce_ohlcv_dates,
    _truncate_ohlcv_to_effective_date,
    filter_financials_by_date,
    load_ohlcv,
    render_indicator_window,
    yf_retry,
)
from .symbol_utils import NoMarketDataError, normalize_symbol


def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):

    datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Resolve broker/forex symbols to Yahoo's convention (XAUUSD+ -> GC=F).
    canonical = normalize_symbol(symbol)
    ticker = yf.Ticker(canonical)

    # yfinance treats ``end`` as EXCLUSIVE, so it would drop the requested
    # end_date row (and the current day when end_date is today). Request one day
    # past end_date so the requested range is actually inclusive (#986/#987).
    end_inclusive = (end_dt + relativedelta(days=1)).strftime("%Y-%m-%d")
    data = yf_retry(
        lambda: ticker.history(
            start=start_date,
            end=end_inclusive,
            auto_adjust=True,
        )
    )

    # Empty result means the symbol is unknown/delisted. Raise a typed error
    # instead of returning prose: the routing layer turns it into a single
    # unambiguous "no data" signal so the agent never fabricates a price.
    if data.empty:
        raise NoMarketDataError(
            symbol, canonical, f"no rows between {start_date} and {end_date}"
        )

    # Remove timezone info from index for cleaner output
    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)

    data = _truncate_ohlcv_to_effective_date(data, end_date, symbol, canonical)
    if data.empty:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"no completed market rows on or before {end_date}",
        )

    # Reject a stale frame (e.g. a year-old partial response) before it is
    # formatted into the report. Raises NoMarketDataError, which the router
    # turns into one clear unavailable signal (#1021).
    _assert_ohlcv_not_stale(data, end_date, symbol, canonical)

    # Round numerical values to 2 decimal places for cleaner display
    numeric_columns = ["Open", "High", "Low", "Close", "Adj Close"]
    for col in numeric_columns:
        if col in data.columns:
            data[col] = data[col].round(2)

    # Convert DataFrame to CSV string
    csv_string = data.to_csv()

    # Add header information; note the resolved symbol when it differs so the
    # agent (and user) can see which instrument was actually priced.
    label = canonical if canonical == symbol.upper() else f"{canonical} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += "# Price adjustment: auto-adjusted prices (yfinance auto_adjust=True)\n"
    header += "# Actual data source: yfinance\n"
    latest = _coerce_ohlcv_dates(data).max().strftime("%Y-%m-%d")
    header += f"# Requested end date: {end_date}\n"
    header += f"# Effective trading date: {latest}\n"
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + csv_string

def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:

    if indicator not in INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(INDICATOR_DESCRIPTIONS.keys())}"
        )

    # Fetch OHLCV once and render the indicator window via the shared,
    # vendor-neutral helper (also used by the J-Quants path) so every vendor's
    # report has an identical shape.
    try:
        data = load_ohlcv(symbol, curr_date)
        rendered = render_indicator_window(data, indicator, curr_date, look_back_days)
        canonical = normalize_symbol(symbol)
        if canonical.endswith((".SS", ".SZ")):
            dates = _coerce_ohlcv_dates(data)
            effective = dates.max().strftime("%Y-%m-%d") if not dates.empty else "n/a"
            rendered = (
                "# Actual data source: yfinance\n"
                "# Price adjustment: auto-adjusted prices (yfinance auto_adjust=True)\n"
                f"# Requested analysis date: {curr_date}\n"
                f"# Effective trading date: {effective}\n\n"
                + rendered
            )
        return rendered
    except NoMarketDataError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except Exception as e:
        print(f"Error getting bulk stockstats data: {e}")

    # Fallback to per-day computation if the bulk path failed.
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)
    ind_string = ""
    day = curr_date_dt
    while day >= before:
        indicator_value = get_stockstats_indicator(
            symbol, indicator, day.strftime("%Y-%m-%d")
        )
        ind_string += f"{day.strftime('%Y-%m-%d')}: {indicator_value}\n"
        day = day - relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + ind_string
        + "\n\n"
        + INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )


def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
) -> str:

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date_dt.strftime("%Y-%m-%d")

    try:
        indicator_value = StockstatsUtils.get_stock_stats(
            symbol,
            indicator,
            curr_date,
        )
    except NoMarketDataError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except Exception as e:
        print(
            f"Error getting stockstats indicator data for indicator {indicator} on {curr_date}: {e}"
        )
        return ""

    return str(indicator_value)


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "requested analysis date; live .info has no history"] = None
):
    """Get a live company overview, refusing to inject it into old backtests."""
    canonical = normalize_symbol(ticker)
    if curr_date is not None and not is_near_live(curr_date, canonical):
        return (
            f"LIVE_DATA_UNAVAILABLE: yfinance .info for {canonical} is a current "
            f"snapshot, not point-in-time historical data, and was not requested "
            f"for historical analysis date {curr_date}. Use get_balance_sheet, "
            "get_cashflow, and get_income_statement for date-filtered statements; "
            "do not estimate missing overview values."
        )
    try:
        ticker_obj = yf.Ticker(canonical)
        info = yf_retry(lambda: ticker_obj.info)

        if not info:
            raise NoMarketDataError(ticker, canonical, "no fundamentals returned")

        fields = [
            ("Name", info.get("longName")),
            ("Sector", info.get("sector")),
            ("Industry", info.get("industry")),
            ("Market Cap", info.get("marketCap")),
            ("PE Ratio (TTM)", info.get("trailingPE")),
            ("Forward PE", info.get("forwardPE")),
            ("Analyst Recommendation", info.get("recommendationKey")),
            ("Analyst Recommendation Mean", info.get("recommendationMean")),
            ("Analyst Opinion Count", info.get("numberOfAnalystOpinions")),
            ("Analyst Target Mean Price", info.get("targetMeanPrice")),
            ("Analyst Target High Price", info.get("targetHighPrice")),
            ("Analyst Target Low Price", info.get("targetLowPrice")),
            ("PEG Ratio", info.get("pegRatio")),
            ("Price to Book", info.get("priceToBook")),
            ("EPS (TTM)", info.get("trailingEps")),
            ("Forward EPS", info.get("forwardEps")),
            ("Dividend Yield", info.get("dividendYield")),
            ("Beta", info.get("beta")),
            ("52 Week High", info.get("fiftyTwoWeekHigh")),
            ("52 Week Low", info.get("fiftyTwoWeekLow")),
            ("50 Day Average", info.get("fiftyDayAverage")),
            ("200 Day Average", info.get("twoHundredDayAverage")),
            ("Revenue (TTM)", info.get("totalRevenue")),
            ("Gross Profit", info.get("grossProfits")),
            ("EBITDA", info.get("ebitda")),
            ("Net Income", info.get("netIncomeToCommon")),
            ("Profit Margin", info.get("profitMargins")),
            ("Operating Margin", info.get("operatingMargins")),
            ("Return on Equity", info.get("returnOnEquity")),
            ("Return on Assets", info.get("returnOnAssets")),
            ("Debt to Equity", info.get("debtToEquity")),
            ("Current Ratio", info.get("currentRatio")),
            ("Book Value", info.get("bookValue")),
            ("Free Cash Flow", info.get("freeCashflow")),
        ]

        lines = []
        for label, value in fields:
            if value is not None:
                lines.append(f"{label}: {value}")

        # yfinance returns a stub dict (e.g. {"trailingPegRatio": None}) for
        # unknown symbols, so `info` is truthy but every field is empty. Treat
        # "no usable fields" as no data rather than emitting a bare header the
        # agent might fabricate around.
        if not lines:
            raise NoMarketDataError(ticker, canonical, "no fundamental fields returned")

        retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        requested = curr_date or retrieved_at[:10]
        header = f"# Company Fundamentals for {canonical} (live yfinance snapshot)\n"
        header += f"# Requested analysis date: {requested}\n"
        header += f"# Retrieved at: {retrieved_at}\n"
        header += "# Not point-in-time historical data.\n\n"

        return header + "\n".join(lines)

    except NoMarketDataError:
        raise
    except VendorRateLimitError as exc:
        # The bounded Incremental journey treats a real 429 as a stop signal.
        # Ordinary Full analysis retains its established rendered-unavailable
        # behaviour if a caller supplies this vendor error outside that scope.
        if stop_on_rate_limit_requested():
            raise
        return f"Error retrieving fundamentals for {ticker}: {str(exc)}"
    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


# yfinance ``.info`` is one HTTP round-trip returning ALL fields, and both
# live-only JP overlays (analyst forward + analyst ratings) read it for the same
# ticker in the same run. Cache successful fetches (bounded LRU) so the run pays
# a single round-trip; a failure is NOT cached so a transient outage stays
# retryable. Only live/near-today runs reach here (callers gate on look-ahead),
# so a process-lifetime snapshot is acceptable.
_INFO_CACHE = SeriesCache(max_entries=256)


def _yf_info(canonical: str) -> dict:
    """Fetch (and memoize) yfinance ``.info`` for a canonical symbol; ``{}`` on failure."""
    info = _INFO_CACHE.get(canonical)
    if info is not None:
        return info
    try:
        info = yf_retry(lambda: yf.Ticker(canonical).info)
    except Exception:
        return {}
    if not info:
        return {}
    _INFO_CACHE.put(canonical, info)
    return info


def get_analyst_forward(ticker: Annotated[str, "ticker symbol of the company"]):
    """Return ``(forward_eps, num_analysts)`` from yfinance ``.info``, else ``(None, None)``.

    The analyst-consensus forward EPS and the number of contributing analysts.
    This is a LIVE snapshot — yfinance exposes no as-of history for ``.info`` — so
    callers must gate it on look-ahead (it is used only for the JP assembler's
    live-only analyst-forward overlay). Any fetch failure degrades to ``(None, None)``.
    """
    info = _yf_info(normalize_symbol(ticker))
    return info.get("forwardEps"), info.get("numberOfAnalystOpinions")


# Analyst-consensus rating fields from yfinance ``.info``, in the order a caller
# would present them: the rating, its 1–5 mean, the analyst count, and the
# 12-month price-target band plus the live price the target is measured against.
_RATING_FIELDS = (
    "recommendationKey",
    "recommendationMean",
    "numberOfAnalystOpinions",
    "targetMeanPrice",
    "targetHighPrice",
    "targetLowPrice",
    "currentPrice",
    "regularMarketPrice",  # fallback when .info omits currentPrice for some .T names
)


def get_analyst_ratings(ticker: Annotated[str, "ticker symbol of the company"]) -> dict:
    """Return yfinance analyst-consensus rating fields as a dict, else ``{}``.

    Sell-side rating (buy/hold/sell), its 1–5 mean, the contributing analyst
    count, and the 12-month price-target band. Like :func:`get_analyst_forward`
    this is a LIVE ``.info`` snapshot with no as-of history, so callers must gate
    it on look-ahead (it feeds the JP sentiment analyst's live-only rating
    overlay). Any fetch failure degrades to ``{}``.
    """
    info = _yf_info(normalize_symbol(ticker))
    return {k: info.get(k) for k in _RATING_FIELDS} if info else {}


# yfinance statement attribute per (kind, freq); the raw line-item frame has
# line items as rows and fiscal-period-end timestamps as columns.
_STATEMENT_ATTRS = {
    ("income", "quarterly"): "quarterly_income_stmt",
    ("income", "annual"): "income_stmt",
    ("balance", "quarterly"): "quarterly_balance_sheet",
    ("balance", "annual"): "balance_sheet",
    ("cashflow", "quarterly"): "quarterly_cashflow",
    ("cashflow", "annual"): "cashflow",
}


def _statement_header(title: str, canonical: str, freq: str, curr_date: str | None) -> str:
    """Label yfinance statements with their actual period-end-only time semantics."""
    requested = curr_date or "not provided (treated as live retrieval)"
    retrieved = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"# {title} data for {canonical} ({freq})\n"
        f"# Requested analysis date: {requested}\n"
        f"# Data retrieved on: {retrieved}\n"
        "# Not point-in-time historical data; columns are filtered by fiscal "
        "period end only, not filing/publication timestamp.\n\n"
    )


def _historical_statement_unavailable(
    ticker: str, curr_date: str | None
) -> str | None:
    """Fail closed when a dated historical request reaches current statements.

    ``curr_date=None`` retains the public dataflow's legacy live-retrieval mode;
    graph-facing tools inject the analysis date from workflow state.
    """
    canonical = normalize_symbol(ticker)
    if curr_date is None or is_near_live(curr_date, canonical):
        return None
    return (
        f"HISTORICAL_DATA_UNAVAILABLE: yfinance statements for {canonical} are "
        f"current retrievals without filing timestamps and were not requested for "
        f"historical analysis date {curr_date}. Use a configured point-in-time "
        "statement provider when available; do not estimate missing values."
    )


def get_statement_frame(
    ticker: Annotated[str, "ticker symbol of the company"],
    kind: Annotated[str, "'income' | 'balance' | 'cashflow'"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date YYYY-MM-DD"] = None,
):
    """Return the date-filtered yfinance statement DataFrame, or None.

    Rows are line items, columns are fiscal-period ends filtered to on/before
    ``curr_date``. This does not establish historical publication time and must
    not be described as point-in-time safe. Best-effort: any fetch failure or
    empty result returns None. Exposed for the JP statement assembler, which
    only consumes it for live/near-live analysis.

    ``freq`` is compared exactly to ``"annual"`` (matching the J-Quants summary's
    own check) so the two halves of a JP statement report never disagree on
    annual-vs-quarterly periods.
    """
    attr = _STATEMENT_ATTRS.get((kind, "annual" if freq == "annual" else "quarterly"))
    if attr is None:
        return None
    canonical = normalize_symbol(ticker)
    if curr_date is not None and not is_near_live(curr_date, canonical):
        return None
    try:
        obj = yf.Ticker(canonical)
        data = yf_retry(lambda: getattr(obj, attr))
    except Exception:
        return None
    if data is None or getattr(data, "empty", True):
        return None
    data = filter_financials_by_date(data, curr_date)
    return data if not data.empty else None


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
):
    """Get balance sheet data from yfinance."""
    unavailable = _historical_statement_unavailable(ticker, curr_date)
    if unavailable:
        return unavailable
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_balance_sheet)
        else:
            data = yf_retry(lambda: ticker_obj.balance_sheet)

        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no balance sheet data")

        from .source_observations import publish_yahoo_statement

        publish_yahoo_statement(data, canonical, "balance", freq)
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        return _statement_header("Balance Sheet", canonical, freq, curr_date) + csv_string

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
):
    """Get cash flow data from yfinance."""
    unavailable = _historical_statement_unavailable(ticker, curr_date)
    if unavailable:
        return unavailable
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_cashflow)
        else:
            data = yf_retry(lambda: ticker_obj.cashflow)

        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no cash flow data")

        from .source_observations import publish_yahoo_statement

        publish_yahoo_statement(data, canonical, "cashflow", freq)
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        return _statement_header("Cash Flow", canonical, freq, curr_date) + csv_string

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
):
    """Get income statement data from yfinance."""
    unavailable = _historical_statement_unavailable(ticker, curr_date)
    if unavailable:
        return unavailable
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_income_stmt)
        else:
            data = yf_retry(lambda: ticker_obj.income_stmt)

        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no income statement data")

        from .source_observations import publish_yahoo_statement

        publish_yahoo_statement(data, canonical, "income", freq)
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        return _statement_header("Income Statement", canonical, freq, curr_date) + csv_string

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"]
):
    """Get insider transactions data from yfinance."""
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.insider_transactions)

        # Empty is normal here (many valid symbols have no insider filings),
        # so report it plainly rather than treating the symbol as invalid.
        if data is None or data.empty:
            return f"No insider transactions reported for symbol '{canonical}'"

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Insider Transactions data for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"
