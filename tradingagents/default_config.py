import os
from collections.abc import Mapping
from copy import deepcopy

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    "TRADINGAGENTS_LLM_MAX_RETRIES":      "llm_max_retries",
    "TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS": "ticker_news_lookback_days",
    "TRADINGAGENTS_SOCIAL_LOOKBACK_DAYS":     "social_lookback_days",
    "TRADINGAGENTS_QUICK_REASONING_EFFORT": "quick_reasoning_effort",
    "TRADINGAGENTS_DEEP_REASONING_EFFORT":  "deep_reasoning_effort",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL":   "google_thinking_level",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "TRADINGAGENTS_ANTHROPIC_EFFORT":        "anthropic_effort",
}


_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")
def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(
    config: dict,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Apply TRADINGAGENTS_* values at an explicit application boundary."""
    env = os.environ if environ is None else environ
    for env_var, key in _ENV_OVERRIDES.items():
        raw = env.get(env_var)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


_BASE_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "data_cache_dir": os.path.join(_TRADINGAGENTS_HOME, "cache"),
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    # Role-specific values take precedence over the provider's legacy shared
    # key. "provider_default" explicitly omits the native SDK parameter.
    "quick_reasoning_effort": None,
    "deep_reasoning_effort": None,
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
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
    "sentiment_filing_limit": 20,         # max low-frequency filing signals
    # Offset from the injected analysis date; endpoints are inclusive, so 14
    # covers 15 calendar dates. News Analyst can explicitly expand to 90 dates.
    "ticker_news_lookback_days": 14,
    "social_lookback_days": 7,            # recent StockTwits/Reddit sentiment window
    "global_news_article_limit": 10,      # max articles for global/macro news
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
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
}


def build_default_config(
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Return a fresh config, optionally applying an explicit environment."""
    config = deepcopy(_BASE_CONFIG)
    if environ is not None:
        _apply_env_overrides(config, environ)
    return config


# Imports are deterministic. Entry points call ``build_default_config`` through
# ``AppSettings.from_env`` after they have explicitly loaded environment files.
DEFAULT_CONFIG = build_default_config()
