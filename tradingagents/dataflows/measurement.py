"""Small deterministic measurement helpers shared by data producers."""

from __future__ import annotations

import re


def instrument_currency(symbol: str) -> str:
    """Return the quote currency implied by the normalized instrument symbol."""

    normalized = symbol.upper()
    for suffix, currency in (
        (".T", "JPY"),
        (".SS", "CNY"),
        (".SZ", "CNY"),
        (".HK", "HKD"),
        (".L", "GBP"),
        (".TO", "CAD"),
        (".AX", "AUD"),
        (".NS", "INR"),
        (".BO", "INR"),
    ):
        if normalized.endswith(suffix):
            return currency
    index_currency = {
        "^N225": "JPY",
        "^HSI": "HKD",
        "^FTSE": "GBP",
        "^GSPTSE": "CAD",
        "^AXJO": "AUD",
        "^NSEI": "INR",
        "^BSESN": "INR",
    }
    if normalized in index_currency:
        return index_currency[normalized]
    if normalized.endswith("=X") and len(normalized) >= 8:
        return normalized[3:6]
    if "-" in normalized:
        quote = normalized.rsplit("-", 1)[-1]
        if len(quote) == 3 and quote.isalpha():
            return quote
    return "USD"


def classify_vendor_unit(unit: str | None) -> tuple[str, str | None]:
    """Classify an explicit vendor unit without inferring from a metric name."""

    value = (unit or "").strip()
    if not value:
        return "unknown", None
    normalized = value.casefold()
    if normalized in {"%", "percent", "percentage points", "pct"}:
        return "percent", value
    if normalized in {"bp", "bps", "basis point", "basis points"}:
        return "basis_points", value
    if normalized in {"x", "times", "ratio"}:
        return "ratio", "x" if normalized in {"x", "times"} else value
    if re.fullmatch(r"(?:19|20)\d{2}\s*=\s*100", value):
        return "index", value
    if normalized in {"index", "index points", "points"}:
        return "index", value
    if re.fullmatch(r"[A-Z]{3}", value):
        return "currency", value
    return "unknown", value
