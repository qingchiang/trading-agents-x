"""Strict interpretation of provider instrument-eligibility responses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .errors import (
    InstrumentEligibilityUnavailableError,
    UnsupportedInstrumentError,
)

_SYMBOL_KEYS = (
    "symbol",
    "ticker",
    "instrument_key",
    "instrumentKey",
    "canonical_symbol",
    "canonicalSymbol",
)
_CLASSIFICATION_KEYS = (
    "quote_type",
    "quoteType",
    "security_type",
    "securityType",
    "instrument_type",
    "instrumentType",
    "asset_type",
    "assetType",
    "classification",
    "type",
)
_EQUITY_CLASSIFICATIONS = frozenset(
    {
        "equity",
        "equities",
        "stock",
        "stocks",
        "common stock",
        "ordinary share",
        "ordinary shares",
    }
)
_KNOWN_NON_EQUITY_CLASSIFICATIONS = frozenset(
    {
        "etf",
        "fund",
        "mutual fund",
        "mutualfund",
        "closed-end fund",
        "index",
        "future",
        "futures",
        "option",
        "options",
        "warrant",
        "forex",
        "currency",
        "crypto",
        "cryptocurrency",
        "digital asset",
        "commodity",
        "fx",
        "bond",
        "bonds",
        "preferred stock",
        "preferred share",
        "trust",
    }
)


def _records(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    """Return provider records without interpreting a fuzzy result as exact."""
    if isinstance(value, Mapping):
        for key in ("results", "quotes", "matches", "items"):
            nested = value.get(key)
            if nested is not None:
                return _records(nested)
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        records = tuple(item for item in value if isinstance(item, Mapping))
        return records if len(records) == len(value) else None
    return None


def _field(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _field_values(record: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(record[key] for key in keys if key in record)


def _normalized_text(value: Any) -> str | None:
    if hasattr(value, "value"):
        value = value.value
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    return value or None


def validate_instrument_eligibility(
    canonical_symbol: str,
    result: Any,
) -> None:
    """Validate one resolver result for exact canonical equity admission.

    Resolver adapters may return one mapping or a sequence of mappings.  A
    sequence with more than one candidate is deliberately ambiguous even when
    one candidate happens to look like an equity.  This function returns no
    provider metadata: qualification metadata is an admission decision only,
    never part of the sealed research Evidence bundle.
    """
    records = _records(result)
    if records is None or len(records) != 1:
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "empty, malformed, or ambiguous resolver result",
        )
    record = records[0]
    if record.get("_malformed") is True:
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "resolver returned a malformed candidate",
        )
    symbols = _field_values(record, _SYMBOL_KEYS)
    if any(not isinstance(value, str) for value in symbols):
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "resolver returned a malformed symbol identity",
        )
    if len({value.casefold() for value in symbols if isinstance(value, str)}) > 1:
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "resolver returned conflicting symbol identities",
        )
    returned_symbol = _field(record, _SYMBOL_KEYS)
    if not isinstance(returned_symbol, str) or (
        returned_symbol.strip().casefold() != canonical_symbol.casefold()
    ):
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "resolver did not return the exact canonical symbol",
        )
    if (
        record.get("exact") is False
        or record.get("is_exact") is False
        or record.get("fuzzy") is True
        or record.get("is_fuzzy") is True
    ):
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "resolver returned a fuzzy identity",
        )

    raw_classifications = _field_values(record, _CLASSIFICATION_KEYS)
    if not raw_classifications:
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "security classification is absent or unknown",
        )
    classifications: set[str] = set()
    for value in raw_classifications:
        classification = _normalized_text(value)
        if classification is None:
            raise InstrumentEligibilityUnavailableError(
                canonical_symbol,
                "resolver returned a malformed security classification",
            )
        classifications.add(classification)
    if len(classifications) > 1:
        raise InstrumentEligibilityUnavailableError(
            canonical_symbol,
            "resolver returned conflicting security classifications",
        )
    classification = next(iter(classifications), None)
    if classification in _EQUITY_CLASSIFICATIONS:
        return
    if classification in _KNOWN_NON_EQUITY_CLASSIFICATIONS:
        raise UnsupportedInstrumentError(canonical_symbol, classification)
    raise InstrumentEligibilityUnavailableError(
        canonical_symbol,
        "security classification is absent or unknown",
    )


__all__ = ["validate_instrument_eligibility"]
