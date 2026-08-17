"""Stable application errors exposed by the research admission boundary."""

from __future__ import annotations


class InstrumentEligibilityError(Exception):
    """Base class for failures while admitting a research instrument."""

    code = "instrument_eligibility_error"


class UnsupportedInstrumentError(InstrumentEligibilityError, ValueError):
    """The resolver positively identified a non-equity instrument."""

    code = "unsupported_instrument"
    status_code = 422

    def __init__(self, symbol: str, classification: str) -> None:
        self.symbol = symbol
        self.classification = classification
        super().__init__(
            f"Instrument {symbol!r} is not a supported listed equity "
            f"({classification})."
        )


class InstrumentEligibilityUnavailableError(
    InstrumentEligibilityError,
    RuntimeError,
):
    """The instrument could not be positively classified as an equity."""

    code = "instrument_eligibility_unavailable"
    status_code = 503

    def __init__(self, symbol: str, reason: str = "classification unavailable"):
        self.symbol = symbol
        self.reason = reason
        super().__init__(
            f"Eligibility for instrument {symbol!r} is temporarily unavailable; "
            "please retry later."
        )


# Short aliases make the distinction convenient for callers while retaining
# one canonical class name and one stable error code.
EligibilityUnavailableError = InstrumentEligibilityUnavailableError


__all__ = [
    "EligibilityUnavailableError",
    "InstrumentEligibilityError",
    "InstrumentEligibilityUnavailableError",
    "UnsupportedInstrumentError",
]
