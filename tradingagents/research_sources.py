"""Stable source identifiers shared across research and dataflow layers."""

from enum import StrEnum


class JapaneseResearchSource(StrEnum):
    """Persisted Japanese Research Source IDs.

    Values are durable provenance identifiers. Renaming an enum member is safe;
    changing its value requires a persistence compatibility decision.
    """

    EDINET = "EDINET"
    TDNET = "TDnet"
    JQUANTS_FUNDAMENTALS = "J-Quants fundamentals"
    JQUANTS_ADJUSTED_OHLCV = "J-Quants adjusted OHLCV"
    GOOGLE_NEWS = "Google News"
    SOCIAL_SENTIMENT = "Social sentiment"
    MACRO_OBSERVATIONS = "Macro observations"


JAPANESE_EVENT_SOURCES = (
    JapaneseResearchSource.EDINET,
    JapaneseResearchSource.TDNET,
)
