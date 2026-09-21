import os
from copy import deepcopy

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

_BASE_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "data_cache_dir": os.path.join(_TRADINGAGENTS_HOME, "cache"),
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # SDK retry budget forwarded to every provider chat client. None leaves each
    # provider/SDK at its own default (usually 2). Raise it to ride out bursty
    # 429 throttling on rate-limited deployments instead of aborting a run (#1091).
    "llm_max_retries": None,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 30,             # max articles per ticker (ticker-news)
    "yahoo_news_candidate_limit": 200,
    "cn_news_candidate_limit": 100,
    "news_selection_version": "2-temporal",
    "news_cache_enabled": True,
    "news_cache_refresh_seconds": 900,
    "news_cache_retention_days": 90,
    "news_cache_scope_limit": 2000,
    "news_cache_total_limit": 50000,
    "sentiment_filing_limit": 20,         # max low-frequency filing signals
    # Offset from the injected analysis date; endpoints are inclusive, so 14
    # covers 15 calendar dates. News Analyst can explicitly expand to 90 dates.
    "ticker_news_lookback_days": 14,
    "social_lookback_days": 7,            # recent StockTwits/Reddit sentiment window
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_candidate_limit": 10,
    "global_news_query_limit": 5,
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    "data_vendors": {
        "instrument_eligibility": "yfinance",  # Current product admission
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance
        # "macro" dispatches each indicator to its owning source: fred (US series
        # + raw FRED IDs; needs FRED_API_KEY), e-Stat (Japan CPI), BOJ (Japan
        # policy rate / Tankan, keyless), and China macro (keyless). Set "fred"
        # to force US-only. See macro.py.
        "macro_data": "macro",

        "prediction_markets": "polymarket",  # Options: polymarket (keyless)
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Market-specific vendor overrides, keyed by ticker exchange suffix (e.g.
    # ".T" for Tokyo, ".SS"/".SZ" for China). When a ticker carries a configured
    # suffix, that category's vendor comes from here instead of ``data_vendors``.
    # Only per-instrument (ticker-bearing) tools are routed; macro and global
    # news stay market-agnostic (cross-border context analyzed across all markets
    # at once) and always use ``data_vendors``. Japanese-market vendors are wired
    # in for ".T" (Tokyo). China phase 2 routes Shanghai/Shenzhen prices and
    # indicators through AkShare first (Tencent qfq, then Eastmoney qfq), with
    # yfinance as the configured final fallback.
    # China fundamentals assemble CNINFO/Sina data before yfinance degradation.
    # These are true ordered fallback chains, distinct from macro_data's per-owner
    # dispatch; don't "fix" one into the other. For JP prices, indicators, and
    # fundamentals, yfinance is
    # OPTIONAL keyless degradation: jquants serves every method when a key is set,
    # and Yahoo (which covers Tokyo) keeps a keyless ".T" run working instead of
    # hard-erroring. For news_data yfinance is also the SOLE server of
    # get_insider_transactions (edinet_news has no insider source), so it is
    # load-bearing even with keys present — don't drop it.
    "data_vendors_by_market": {
        ".T": {
            "core_stock_apis": "jquants,yfinance",
            "technical_indicators": "jquants,yfinance",
            # get_fundamentals goes to jp_fundamentals (J-Quants summary + date-safe
            # computed valuation ratios); the three statement methods go to
            # jp_statements (J-Quants summary + curated yfinance line items). Each
            # JP assembler serves only its own methods, so the router picks the
            # right one per method; jquants then yfinance remain keyless fallbacks.
            "fundamental_data": "jp_fundamentals,jp_statements,jquants,yfinance",
            # jp_news assembles EDINET statutory filings + Google-News media
            # headlines (edinet alone would win the fallback and hide the media
            # side); yfinance (English media) stays a keyless last resort.
            "news_data": "jp_news,yfinance",
        },
        ".SS": {
            "core_stock_apis": "akshare,yfinance",
            "technical_indicators": "akshare,yfinance",
            "fundamental_data": "cn_fundamentals,cn_statements,akshare,yfinance",
            "news_data": "cn_news,yfinance",
        },
        ".SZ": {
            "core_stock_apis": "akshare,yfinance",
            "technical_indicators": "akshare,yfinance",
            "fundamental_data": "cn_fundamentals,cn_statements,akshare,yfinance",
            "news_data": "cn_news,yfinance",
        },
    },
}


def build_default_config() -> dict:
    """Return isolated data defaults without reading or applying environment values."""
    return deepcopy(_BASE_CONFIG)


# Imports are deterministic. These program defaults also back the typed
# configuration catalog; environment values enter daily settings only by import.
DEFAULT_CONFIG = build_default_config()
