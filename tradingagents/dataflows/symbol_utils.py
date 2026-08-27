"""Symbol normalization and market-data error types for vendor calls.

Yahoo Finance (the default vendor) uses specific ticker conventions that
differ from the broker / TradingView / MT5 style symbols users often type:

    user types        Yahoo wants       why
    ---------------   ---------------   -----------------------------------
    XAUUSD, XAUUSD+   GC=F              gold has no forex pair on Yahoo;
                                        it is quoted as a COMEX future
    EURUSD            EURUSD=X          spot forex pairs take a ``=X`` suffix
    SPX500, US500     ^GSPC             index CFDs map to Yahoo index symbols

Passing the raw broker symbol to Yahoo returns an empty result, which the
agents previously received as free text and could hallucinate a price
around (see issue #781). Centralizing the mapping here means every yfinance
entry point resolves symbols the same way, and new instruments are added by
appending a table row rather than editing call sites.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo

# NoMarketDataError lives in the vendor-error taxonomy (errors.py); re-exported
# here for the many call sites that import it alongside normalize_symbol.
from .errors import NoMarketDataError as NoMarketDataError

logger = logging.getLogger(__name__)


# ISO-4217 codes common enough to appear in retail forex pairs. A bare
# six-letter symbol whose halves are BOTH in this set is treated as a spot
# forex pair and given Yahoo's ``=X`` suffix.
_FOREX_CURRENCIES = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
        "CNY", "CNH", "HKD", "SGD", "SEK", "NOK", "DKK", "PLN",
        "MXN", "ZAR", "TRY", "INR", "KRW", "BRL", "RUB", "THB",
    }
)

# Explicit aliases for instruments whose broker symbol does not map to a
# Yahoo symbol by rule. Metals/energy resolve to their front-month future;
# index CFD names resolve to the underlying Yahoo index symbol. Extend by
# adding rows — no call site changes required.
_ALIASES = {
    # Precious metals (spot names -> COMEX/NYMEX futures)
    "XAUUSD": "GC=F", "XAU": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "XAG": "SI=F", "SILVER": "SI=F",
    "XPTUSD": "PL=F", "XPDUSD": "PA=F",
    # Energy
    "WTICOUSD": "CL=F", "USOIL": "CL=F", "WTI": "CL=F",
    "BCOUSD": "BZ=F", "UKOIL": "BZ=F", "BRENT": "BZ=F",
    "NATGAS": "NG=F", "XNGUSD": "NG=F",
    "COPPER": "HG=F", "XCUUSD": "HG=F",
    # Index CFDs -> Yahoo index symbols
    "SPX500": "^GSPC", "US500": "^GSPC", "SPX": "^GSPC",
    "NAS100": "^NDX", "US100": "^NDX", "USTEC": "^NDX",
    "US30": "^DJI", "DJI30": "^DJI", "WS30": "^DJI",
    "GER40": "^GDAXI", "GER30": "^GDAXI", "DE40": "^GDAXI",
    "UK100": "^FTSE", "JP225": "^N225", "JPN225": "^N225",
    "FRA40": "^FCHI", "EU50": "^STOXX50E", "HK50": "^HSI",
}

# Yahoo symbols may contain letters, digits, and these structural characters.
_YAHOO_SAFE = re.compile(r"^[A-Za-z0-9._\-\^=]+$")
_US_EQUITY_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$")
_JAPAN_EQUITY_SYMBOL = re.compile(r"^[A-Z0-9]{4}\.T$")
_UNSUPPORTED_INDEX_ALIASES = frozenset(
    {
        "DJI",
        "GSPC",
        "IXIC",
        "NSEI",
        "BSESN",
        "N225",
        "HSI",
        "FTSE",
        "AXJO",
        "GDAXI",
        "FCHI",
        "NDX",
        "RUT",
        "VIX",
    }
)


# Quote tokens used only to provide a clearer error for compact Crypto-like
# forms.  This is deliberately not a base-token blacklist: a pair with an
# unknown base is still rejected when it uses the unambiguous ``BASE-QUOTE``
# shape below.
_UNSUPPORTED_CRYPTO_QUOTES = frozenset(
    {
        *(_FOREX_CURRENCIES),
        "ADA",
        "AVAX",
        "BCH",
        "BTC",
        "DOGE",
        "DOT",
        "ETH",
        "LINK",
        "LTC",
        "PEPE",
        "SHIB",
        "SOL",
        "USDC",
        "USDT",
        "XRP",
    }
)

# Mainland China A-share prefixes supported by the first China-market branch.
# These are equity boards only: Shanghai main board / STAR Market and Shenzhen
# main board / ChiNext. Funds, bonds, B-shares and Beijing listings deliberately
# remain out of scope, so an otherwise ambiguous bare six-digit code fails loud
# instead of being routed as an unsuffixed US ticker.
_SHANGHAI_A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SHENZHEN_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301")
_MAINLAND_MARKET_BENCHMARKS = frozenset(
    {
        # SSE/SZSE composites and the principal CSI/SSE regional indices.
        "000001.SS",
        "000001.SZ",
        "000016.SS",
        "000300.SS",
        "000688.SS",
        "000905.SS",
        "000852.SS",
        "399001.SZ",
        "399006.SZ",
    }
)
_MAINLAND_MARKET_BENCHMARK_CODES = frozenset(
    symbol.split(".", 1)[0] for symbol in _MAINLAND_MARKET_BENCHMARKS
)

# Calendar timezone used when a date boundary depends on the instrument's
# exchange rather than the host machine. Unsuffixed Yahoo symbols retain the US
# default; adjacent vendor suffixes remain available to low-level dataflows.
_MARKET_TIMEZONES_BY_SUFFIX = {
    ".T": "Asia/Tokyo",
    ".HK": "Asia/Hong_Kong",
    ".SS": "Asia/Shanghai",
    ".SZ": "Asia/Shanghai",
    ".NS": "Asia/Kolkata",
    ".BO": "Asia/Kolkata",
    ".L": "Europe/London",
    ".TO": "America/Toronto",
    ".AX": "Australia/Sydney",
}

# Yahoo's major index symbols do not carry exchange suffixes, so preserve their
# market identity explicitly after alias normalization (for example,
# ``JP225`` -> ``^N225``). Keep this aligned with the supported index aliases
# above and the regional benchmark symbols in ``DEFAULT_CONFIG``.
_MARKET_TIMEZONES_BY_SYMBOL = {
    "^N225": "Asia/Tokyo",
    "^HSI": "Asia/Hong_Kong",
    "^NSEI": "Asia/Kolkata",
    "^BSESN": "Asia/Kolkata",
    "^FTSE": "Europe/London",
    "^GSPTSE": "America/Toronto",
    "^AXJO": "Australia/Sydney",
    "^GDAXI": "Europe/Berlin",
    "^FCHI": "Europe/Paris",
    "^STOXX50E": "Europe/Paris",
}
_DEFAULT_MARKET_TIMEZONE = ZoneInfo("America/New_York")


def infer_mainland_equity_suffix(code: str) -> str | None:
    """Return ``.SS``/``.SZ`` for a supported six-digit A-share equity code."""
    value = str(code)
    if not re.fullmatch(r"\d{6}", value):
        return None
    if value.startswith(_SHANGHAI_A_SHARE_PREFIXES):
        return ".SS"
    if value.startswith(_SHENZHEN_A_SHARE_PREFIXES):
        return ".SZ"
    return None


def _validate_explicit_mainland_suffix(symbol: str) -> None:
    """Reject unsupported mainland security types and wrong equity exchanges."""
    match = re.fullmatch(r"(\d{6})(\.SS|\.SZ)", symbol)
    if match is None:
        return
    if symbol in _MAINLAND_MARKET_BENCHMARKS:
        return
    code, suffix = match.groups()
    expected = infer_mainland_equity_suffix(code)
    if expected is None:
        raise ValueError(
            f"Mainland security {symbol!r} is not supported in the A-share equity "
            "phase; ETFs, funds, bonds, and non-benchmark indices are out of scope."
        )
    if suffix != expected:
        raise ValueError(
            f"Exchange suffix mismatch for {symbol!r}: equity code {code} "
            f"requires {expected}, not {suffix}."
        )


def _normalize_explicit_china_suffix(symbol: str) -> str | None:
    """Normalize supported China aliases and reject explicitly unsupported ones."""
    match = re.fullmatch(r"(\d{6})\.(SH|BJ)", symbol)
    if match is None:
        return None
    code, suffix = match.groups()
    if suffix == "SH":
        return f"{code}.SS"
    raise ValueError(
        f"Beijing Stock Exchange symbol {symbol!r} is not supported; "
        "China market phase 1 supports Shanghai (.SS) and Shenzhen (.SZ) "
        "A-share equities only."
    )


def _normalize_bare_a_share(code: str) -> str | None:
    """Return a Yahoo-style A-share symbol for a bare six-digit equity code.

    ``None`` means the input is not a bare six-digit code. A bare six-digit code
    outside the explicitly supported Shanghai/Shenzhen equity prefixes raises so
    it cannot silently fall through to the US market.
    """
    if not re.fullmatch(r"\d{6}", code):
        return None
    suffix = infer_mainland_equity_suffix(code)
    if suffix is not None:
        return f"{code}{suffix}"
    raise ValueError(
        f"Cannot infer a supported Shanghai/Shenzhen A-share exchange for bare "
        f"code {code!r}. Use an explicit Yahoo-style suffix for a supported "
        "instrument; Beijing listings and non-equity securities are out of scope."
    )


def unsupported_crypto_base(raw: str) -> str | None:
    """Identify a Crypto-like pair so public requests can reject it.

    This helper never normalizes a symbol or makes it vendor-routable.  A
    dashed pair with multi-character base and quote is unambiguously a pair,
    so unknown combinations such as ``DOGE-SHIB`` are rejected without an
    ever-growing base-token denylist.  Compact forms additionally recognize
    common fiat/stablecoin/token quote codes.
    """
    if not isinstance(raw, str):
        return None
    symbol = raw.strip().upper().rstrip("+")
    if "-" in symbol:
        base, quote = symbol.rsplit("-", 1)
        if (
            len(base) >= 2
            and len(quote) >= 2
            and base.isalnum()
            and quote.isalnum()
        ):
            return base
    compact = symbol.replace("-", "")
    if (
        len(compact) == 6
        and compact[:3] in _FOREX_CURRENCIES
        and compact[3:] in _FOREX_CURRENCIES
    ):
        return None
    for quote in sorted(_UNSUPPORTED_CRYPTO_QUOTES, key=len, reverse=True):
        if compact.endswith(quote):
            base = compact[: -len(quote)]
            if base and len(base) >= 2 and base.isalnum():
                return base
    return None


def is_supported_equity_symbol(symbol: str) -> bool:
    """Return whether a canonical symbol is in a supported equity market.

    This is the positive product predicate.  It intentionally does not derive
    support from vendor routing or market-timezone metadata, which cover more
    exchanges and instrument types than the public research product.
    """
    if not isinstance(symbol, str):
        return False
    if symbol in _UNSUPPORTED_INDEX_ALIASES:
        return False
    if _US_EQUITY_SYMBOL.fullmatch(symbol):
        return True
    if _JAPAN_EQUITY_SYMBOL.fullmatch(symbol):
        return True
    match = re.fullmatch(r"(\d{6})(\.SS|\.SZ)", symbol)
    if match is None or symbol in _MAINLAND_MARKET_BENCHMARKS:
        return False
    code, suffix = match.groups()
    if code in _MAINLAND_MARKET_BENCHMARK_CODES:
        return False
    return infer_mainland_equity_suffix(code) == suffix


def normalize_symbol(raw: str) -> str:
    """Map a user/broker symbol to its canonical Yahoo Finance symbol.

    Resolution order (first match wins):
      1. Explicit China suffix: ``CODE.SH`` -> ``CODE.SS``; ``CODE.BJ`` raises.
      2. Bare Shanghai/Shenzhen A-share equity code -> ``CODE.SS``/``CODE.SZ``.
      3. Explicit alias table (metals, energy, index CFDs).
      4. Forex rule: six letters that are two ISO currency codes -> ``PAIR=X``.
      5. Otherwise the upper-cased symbol is returned unchanged (plain
         equities, ETFs, Yahoo-native symbols like ``GC=F`` or ``^GSPC``).

    A trailing ``+`` (broker CFD marker, e.g. ``XAUUSD+``) is stripped before
    matching. The function is purely syntactic — it performs no network
    calls — so it is safe to apply on every request.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw

    s = raw.strip().upper()
    # Broker CFD/qualifier suffixes Yahoo never uses.
    s = s.rstrip("+")

    explicit_china = _normalize_explicit_china_suffix(s)
    a_share = _normalize_bare_a_share(s)
    if explicit_china is not None:
        canonical = explicit_china
    elif a_share is not None:
        canonical = a_share
    elif s in _ALIASES:
        canonical = _ALIASES[s]
    elif len(s) == 6 and s[:3] in _FOREX_CURRENCIES and s[3:] in _FOREX_CURRENCIES:
        canonical = f"{s}=X"
    else:
        canonical = s

    _validate_explicit_mainland_suffix(canonical)
    if canonical != raw.strip().upper():
        logger.info("Resolved symbol %r to Yahoo symbol %r", raw, canonical)
    return canonical


def is_yahoo_safe(symbol: str) -> bool:
    """True when ``symbol`` only contains characters Yahoo symbols use."""
    return bool(symbol) and _YAHOO_SAFE.fullmatch(symbol) is not None


def tokyo_securities_base(code: str) -> str:
    """Reduce a Tokyo securities code to its 4-digit base.

    J-Quants and EDINET both carry the 5-digit listing code (``99840``), while
    the canonical Yahoo ticker uses the 4-digit base (``9984``). Stripping the
    trailing share-class/check digit in one place keeps J-Quants'
    ``from_jquants_code`` and EDINET's secCode matching on the same key, so the
    two never drift out of lockstep. Returns "" for a missing code.
    """
    c = str(code or "").strip()
    if len(c) == 5 and c.endswith("0"):
        c = c[:-1]
    return c


def match_exchange_suffix(symbol: str, suffixes: Iterable[str]) -> str:
    """Return the longest exchange suffix from ``suffixes`` that ``symbol`` ends
    with (case-insensitive), or "" if none match.

    ``suffixes`` is any iterable of suffix strings — typically the keys of
    configured vendor maps. The empty-string entry some maps carry as a default
    is ignored; callers handle the no-suffix fallback.
    Longest-match makes the result deterministic if two configured suffixes ever
    overlap. Single source of truth for exchange-suffix detection so new markets
    (Japan ``.T``, China ``.SS``/``.SZ``) are recognized in one place by both
    vendor routing.
    """
    if not isinstance(symbol, str) or not symbol:
        return ""
    upper = symbol.upper()
    for suffix in sorted((s for s in suffixes if s), key=len, reverse=True):
        if upper.endswith(suffix.upper()):
            return suffix
    return ""


def market_timezone(symbol: str | None) -> tzinfo:
    """Return the calendar timezone used for a Yahoo-compatible instrument."""
    if symbol is None:
        return UTC
    canonical = normalize_symbol(symbol)
    if canonical in _MARKET_TIMEZONES_BY_SYMBOL:
        return ZoneInfo(_MARKET_TIMEZONES_BY_SYMBOL[canonical])
    suffix = match_exchange_suffix(canonical, _MARKET_TIMEZONES_BY_SUFFIX)
    if suffix:
        return ZoneInfo(_MARKET_TIMEZONES_BY_SUFFIX[suffix])
    return _DEFAULT_MARKET_TIMEZONE


def market_today(symbol: str | None, now: datetime | None = None) -> date:
    """Return the instrument's current calendar date, independent of host timezone."""
    market_tz = market_timezone(symbol)
    current = now or datetime.now(market_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=market_tz)
    else:
        current = current.astimezone(market_tz)
    return current.date()
