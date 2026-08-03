"""Deterministic, presentation-only formatting for audited decision numbers."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

_FIAT_UNITS = {
    "$",
    "¥",
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "EUR",
    "GBP",
    "HKD",
    "JPY",
    "KRW",
    "USD",
    "円",
}
_PERCENT_UNITS = {"%", "PCT", "PERCENT"}
_RATIO_UNITS = {"X", "倍"}


def decision_fraction_digits(value: float, unit: str | None) -> int:
    """Return reading precision without changing the canonical numeric value."""

    normalized = (unit or "").strip().upper()
    absolute = abs(value)
    if normalized in _PERCENT_UNITS or normalized in _RATIO_UNITS:
        return 2
    if 0 < absolute < 1:
        magnitude = math.floor(math.log10(absolute))
        return min(8, max(0, 3 - magnitude))
    if normalized in _FIAT_UNITS:
        return 2
    return 4


def format_decision_number(
    value: float,
    unit: str | None = None,
    *,
    output_language: str | None = None,
) -> str:
    """Format a finite value for Web/Markdown while retaining raw audit data."""

    del output_language  # en, zh-CN, and ja share stable decimal/group separators.
    if not math.isfinite(value):
        return str(value)
    digits = decision_fraction_digits(value, unit)
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    rendered = f"{rounded:,.{digits}f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered
