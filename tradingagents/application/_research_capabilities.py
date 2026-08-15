"""Market Research Capability profiles and stable source contracts."""

from tradingagents.research_sources import JapaneseResearchSource

from ._research_models import (
    CapabilitySourceContract,
    MarketResearchCapability,
    MarketResearchCapabilityProfile,
    TransitionContinuityRule,
)

JAPANESE_ANCHOR_PROFILE = MarketResearchCapabilityProfile(
    id="jp-listed-equity-v1",
    instrument_suffixes=(".T",),
    bounded_execution_supported=True,
    minimum_anchor_capabilities=(
        MarketResearchCapability.OFFICIAL_FILING,
        MarketResearchCapability.TIMELY_DISCLOSURE,
        MarketResearchCapability.MARKET_OBSERVATION,
    ),
    source_contracts=(
        CapabilitySourceContract(
            capability=MarketResearchCapability.OFFICIAL_FILING,
            transition_continuity=TransitionContinuityRule.EVENT_STREAM,
            acceptable_source_sets=((JapaneseResearchSource.EDINET,),),
        ),
        CapabilitySourceContract(
            capability=MarketResearchCapability.TIMELY_DISCLOSURE,
            transition_continuity=TransitionContinuityRule.EVENT_STREAM,
            acceptable_source_sets=((JapaneseResearchSource.TDNET,),),
        ),
        CapabilitySourceContract(
            capability=MarketResearchCapability.FUNDAMENTALS,
            transition_continuity=TransitionContinuityRule.SNAPSHOT,
            acceptable_source_sets=((JapaneseResearchSource.JQUANTS_FUNDAMENTALS,),),
        ),
        CapabilitySourceContract(
            capability=MarketResearchCapability.MARKET_OBSERVATION,
            transition_continuity=TransitionContinuityRule.MARKET_SERIES,
            acceptable_source_sets=((JapaneseResearchSource.JQUANTS_ADJUSTED_OHLCV,),),
        ),
        CapabilitySourceContract(
            capability=MarketResearchCapability.MEDIA,
            transition_continuity=TransitionContinuityRule.EVENT_STREAM,
            acceptable_source_sets=((JapaneseResearchSource.GOOGLE_NEWS,),),
        ),
        CapabilitySourceContract(
            capability=MarketResearchCapability.SOCIAL_SENTIMENT,
            transition_continuity=TransitionContinuityRule.SNAPSHOT,
            acceptable_source_sets=((JapaneseResearchSource.SOCIAL_SENTIMENT,),),
        ),
        CapabilitySourceContract(
            capability=MarketResearchCapability.MACRO,
            transition_continuity=TransitionContinuityRule.SNAPSHOT,
            acceptable_source_sets=((JapaneseResearchSource.MACRO_OBSERVATIONS,),),
        ),
    ),
)


def market_research_capability_profile(
    instrument: str,
) -> MarketResearchCapabilityProfile | None:
    if any(instrument.endswith(suffix) for suffix in JAPANESE_ANCHOR_PROFILE.instrument_suffixes):
        return JAPANESE_ANCHOR_PROFILE
    return None
