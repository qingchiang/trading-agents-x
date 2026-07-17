import logging
import re
from datetime import datetime, timedelta, timezone

from tradingagents.provenance import (
    ProvenanceRecord,
    attach_provenance,
    extract_provenance,
)

from .alpha_vantage import (
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_global_news as get_alpha_vantage_global_news,
    get_income_statement as get_alpha_vantage_income_statement,
    get_indicator as get_alpha_vantage_indicator,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_stock as get_alpha_vantage_stock,
    get_verified_market_snapshot as get_alpha_vantage_verified_snapshot,
)
from .config import get_config
from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from .fred import get_macro_data as get_fred_macro_data
from .jp.edinet_news import get_news as get_edinet_news
from .jp.google_news import get_news as get_google_news
from .jp.jp_fundamentals import get_fundamentals as get_jp_fundamentals
from .jp.jp_news import get_news as get_jp_news
from .jp.jp_statements import (
    get_balance_sheet as get_jp_balance_sheet,
    get_cashflow as get_jp_cashflow,
    get_income_statement as get_jp_income_statement,
)
from .jp.jquants import (
    get_balance_sheet as get_jquants_balance_sheet,
    get_cashflow as get_jquants_cashflow,
    get_fundamentals as get_jquants_fundamentals,
    get_income_statement as get_jquants_income_statement,
    get_indicator as get_jquants_indicator,
    get_stock as get_jquants_stock,
    get_verified_market_snapshot as get_jquants_verified_snapshot,
)
from .jp.tdnet_news import get_news as get_tdnet_news
from .macro import get_macro_indicators as get_macro_dispatch
from .market_context import infer_market
from .market_data_validator import (
    build_verified_market_snapshot as get_yfinance_verified_snapshot,
)
from .polymarket import get_prediction_markets as get_polymarket_prediction_markets
from .y_finance import (
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_fundamentals as get_yfinance_fundamentals,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
    get_stock_stats_indicators_window,
    get_YFin_data_online,
)
from .yfinance_news import get_global_news_yfinance, get_news_yfinance

logger = logging.getLogger(__name__)

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators",
            "get_verified_market_snapshot",
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    },
    "macro_data": {
        "description": "Macroeconomic indicators (rates, inflation, labor, growth)",
        "tools": [
            "get_macro_indicators",
        ]
    },
    "prediction_markets": {
        "description": "Market-implied probabilities for forward-looking events",
        "tools": [
            "get_prediction_markets",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "fred",
    "macro",
    "polymarket",
    "alpha_vantage",
    "jquants",
    "jp_fundamentals",
    "jp_statements",
    "edinet_news",
    "tdnet_news",
    "google_news",
    "jp_news",
]

# Optional enrichment categories. These add macro/event context to the news
# analyst but are not core to a decision, so a vendor failure here degrades to a
# sentinel instead of aborting the run (a bad LLM-supplied indicator, a missing
# key, or a network blip should not crash an analysis over flavour data). Core
# categories (prices, fundamentals, news) still raise so a broken primary is loud.
OPTIONAL_CATEGORIES = {"macro_data", "prediction_markets"}

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
        "jquants": get_jquants_stock,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        "jquants": get_jquants_indicator,
    },
    "get_verified_market_snapshot": {
        "alpha_vantage": get_alpha_vantage_verified_snapshot,
        "yfinance": get_yfinance_verified_snapshot,
        "jquants": get_jquants_verified_snapshot,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "jquants": get_jquants_fundamentals,
        "jp_fundamentals": get_jp_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "jquants": get_jquants_balance_sheet,
        "jp_statements": get_jp_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "jquants": get_jquants_cashflow,
        "jp_statements": get_jp_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "jquants": get_jquants_income_statement,
        "jp_statements": get_jp_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        "edinet_news": get_edinet_news,
        "tdnet_news": get_tdnet_news,
        "google_news": get_google_news,
        "jp_news": get_jp_news,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
    # macro_data — the "macro" vendor dispatches by indicator to the owning source
    # (fred / e-Stat / boj); see macro.py. ("fred" stays selectable to force
    # US-only.) Dispatch (one owner per indicator), not a fallback chain, so the
    # owning vendor's typed error degrades with the right reason.
    "get_macro_indicators": {
        "macro": get_macro_dispatch,
        "fred": get_fred_macro_data,
    },
    # prediction_markets
    "get_prediction_markets": {
        "polymarket": get_polymarket_prediction_markets,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None, market: str = "", config: dict = None) -> str:
    """Get the configured vendor for a data category or specific tool method.

    Resolution order (first match wins):
      1. Tool-level config (``tool_vendors[method]``).
      2. Market-specific category config (``data_vendors_by_market[market][category]``),
         e.g. ``.T`` routes Japanese tickers to JP vendors.
      3. Default category config (``data_vendors[category]``).

    ``market=""`` skips step 2, reproducing the original behavior exactly, so
    US / unsuffixed tickers are unaffected. ``config`` may be passed to reuse a
    snapshot the caller already loaded (``get_config()`` deep-copies).
    """
    if config is None:
        config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Market-specific override, keyed by ticker exchange suffix.
    if market:
        by_market = config.get("data_vendors_by_market", {}).get(market, {})
        if category in by_market:
            return by_market[category]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")


def _append_availability_notes(result, notes: list[str]):
    """Attach composite-source warnings to a textual fallback result."""
    if not notes or not isinstance(result, str):
        return result
    unique_notes = list(dict.fromkeys(notes))
    return (
        f"{result.rstrip()}\n\n### Source availability notes\n"
        + "\n".join(unique_notes)
    )


def _provenance_for_route(
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
        effective = f"latest trading data <= {args[2]}"
        timing = "market-date filtered"
    elif method == "get_verified_market_snapshot" and len(args) >= 2:
        requested = str(args[1])
        effective = f"latest trading data <= {args[1]}"
        timing = "market-date filtered"
    elif method == "get_news" and len(args) >= 3:
        requested = f"{args[1]} to {args[2]}"
        effective = requested
        timing = (
            "publication/disclosure-date filtered; "
            f"returned_items={result.count(chr(10) + '### ')}"
        )
    elif method == "get_global_news" and args:
        end_date = str(args[0])
        lookback = args[1] if len(args) > 1 and args[1] is not None else config["global_news_lookback_days"]
        try:
            start_date = (
                datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=int(lookback))
            ).strftime("%Y-%m-%d")
            requested = f"{start_date} to {end_date}"
        except (TypeError, ValueError):
            requested = f"ending {end_date}"
        effective = requested
        timing = (
            "publication-date filtered; "
            f"returned_items={result.count(chr(10) + '### ')}"
        )
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
            retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        else:
            timing = "disclosure-date filtered"
    elif method in {"get_balance_sheet", "get_cashflow", "get_income_statement"}:
        curr_date = args[2] if len(args) >= 3 else None
        requested = str(curr_date or "live retrieval")
        effective = (
            f"fiscal period ends <= {curr_date}"
            if curr_date
            else "current statement frame"
        )
        if "LIVE_DATA_UNAVAILABLE" in result:
            timing = "unavailable for historical date; vendor not queried"
        elif vendor in {"yfinance", "alpha_vantage"}:
            timing = "period-end filtered only; not point-in-time"
            retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        else:
            timing = "disclosure-date filtered"

    lowered = result.casefold()
    if "live_data_unavailable" in lowered:
        effective = "—"
        timing = "unavailable for historical date; vendor not queried"
        retrieved_at = None
    elif "data_unavailable" in lowered or "error fetching" in lowered or "error retrieving" in lowered:
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


def _attach_unavailable_provenance(
    result: str,
    method: str,
    vendors: list[str],
    args: tuple,
    config: dict,
    timing: str,
) -> str:
    record = _provenance_for_route(
        method, " / ".join(vendors) or "unknown", args, config, result
    )
    if record is None:
        return result
    return attach_provenance(
        result,
        ProvenanceRecord(
            evidence=record.evidence,
            source=record.source,
            requested=record.requested,
            effective="—",
            timing=timing,
        ),
    )


def route_to_vendor(method: str, *args, _provenance: bool = False, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    category = get_category_for_method(method)
    # Suffix-based routing: ticker-bearing methods infer the market from their
    # first arg; ticker-less ones are market-agnostic (market=""). Read config
    # once and thread it through so the per-call deep-copy happens a single time.
    config = get_config()
    market = infer_market(method, args, config.get("data_vendors_by_market", {}))
    vendor_config = get_vendor(category, method, market, config)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    all_available_vendors = list(VENDOR_METHODS[method].keys())

    # The configured vendor list IS the chain: we do NOT silently fall back to
    # vendors the user did not choose (#988/#289) — that returned data from an
    # unexpected source and caused cross-vendor inconsistencies. For multi-vendor
    # fallback, list them in order, e.g. data_vendors="yfinance,alpha_vantage".
    # The "default" sentinel (no explicit config) uses all available vendors.
    explicit = [v for v in primary_vendors if v and v != "default"]
    if explicit:
        vendor_chain = [v for v in explicit if v in VENDOR_METHODS[method]]
        if not vendor_chain:
            raise ValueError(
                f"Configured vendor(s) {explicit} not available for '{method}'. "
                f"Available: {all_available_vendors}."
            )
    else:
        vendor_chain = all_available_vendors

    last_no_data: NoMarketDataError | None = None
    first_error: Exception | None = None
    availability_notes: list[str] = []
    for vendor in vendor_chain:
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            result = impl_func(*args, **kwargs)
            result = _append_availability_notes(result, availability_notes)
            if _provenance and isinstance(result, str) and not extract_provenance(result):
                record = _provenance_for_route(
                    method, vendor, args, config, result
                )
                if record is not None:
                    result = attach_provenance(result, record)
            return result
        except VendorRateLimitError:
            logger.warning("Vendor %r rate-limited for %s; trying next vendor.", vendor, method)
            continue
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next vendor.", vendor, method)
            if first_error is None:
                first_error = e  # Surface it if no other vendor can serve the call.
            continue
        except NoMarketDataError as e:
            last_no_data = e  # No data here; another configured vendor may have it
            availability_notes.extend(e.availability_notes)
            continue
        except Exception as e:
            # Don't let one vendor's failure crash the call when another can
            # serve it, but never swallow silently: a broken primary must be
            # visible in the logs (#989), not hidden behind a fallback's verdict.
            logger.warning("Vendor %r failed for %s: %s", vendor, method, e)
            if first_error is None:
                first_error = e
            continue

    # If any vendor reported "no data", the symbol is genuinely unavailable.
    # Return one explicit, instructive sentinel rather than a vendor-specific
    # empty string, so the agent reports "unavailable" instead of inventing a
    # value. This takes precedence over incidental fallback errors.
    if last_no_data is not None:
        if first_error is not None:
            # A vendor also hit a real error; surface it in logs so the no-data
            # verdict can't hide a broken primary (network/auth/etc.).
            logger.warning(
                "Returning NO_DATA for %s, but a vendor errored earlier: %s",
                method, first_error,
            )
        sym = last_no_data.symbol
        canonical = last_no_data.canonical
        resolved = "" if canonical == sym else f" (resolved to '{canonical}')"
        # Surface the typed error's detail (e.g. "latest row is 2025-06-11 ...
        # stale") so the agent sees the specific reason — invalid symbol, no
        # coverage, or stale data — not just a generic "unavailable".
        reason = f" ({last_no_data.detail})" if last_no_data.detail else ""
        result = (
            f"NO_DATA_AVAILABLE: No usable market data for '{sym}'{resolved} from "
            f"any configured vendor{reason}. The symbol may be invalid, delisted, "
            f"not covered, or the vendor returned stale data. Do not estimate or "
            f"fabricate values — report that data is unavailable for this symbol."
        )
        result = _append_availability_notes(result, availability_notes)
        return (
            _attach_unavailable_provenance(
                result,
                method,
                vendor_chain,
                args,
                config,
                "no usable data from configured vendors",
            )
            if _provenance
            else result
        )

    # No vendor returned data and none reported clean "no data" — surface the
    # first real error (e.g. the primary vendor's network failure). Optional
    # enrichment categories degrade to a sentinel instead, so flavour data can't
    # abort the run.
    if first_error is not None:
        if category in OPTIONAL_CATEGORIES:
            logger.warning("Optional %s unavailable for %s: %s", category, method, first_error)
            result = (
                f"DATA_UNAVAILABLE: optional {category} could not be retrieved "
                f"({first_error}). Proceed without it; do not fabricate values."
            )
            return (
                _attach_unavailable_provenance(
                    result,
                    method,
                    vendor_chain,
                    args,
                    config,
                    "retrieval unavailable",
                )
                if _provenance
                else result
            )
        raise first_error

    raise RuntimeError(f"No available vendor for '{method}'")
