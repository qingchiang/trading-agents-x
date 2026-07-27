"""Structured schemas used by evidence-collection analysts."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SentimentBand(str, Enum):
    """Discrete direction produced by the sentiment analyst."""

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentReport(BaseModel):
    """Machine-readable sentiment summary with a human narrative."""

    overall_band: SentimentBand = Field(
        description=(
            "Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / "
            "Mildly Bearish / Bearish. Use Mixed for conflicting sources and "
            "Neutral only when sources are genuinely non-directional."
        ),
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Direction and intensity from 0 (bearish) through 5 (neutral) "
            "to 10 (bullish)."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Confidence based on source coverage and data quality.",
    )
    narrative: str = Field(
        description=(
            "Source-by-source evidence, divergences, catalysts, risks, and a "
            "Markdown summary table. Do not include account or trade instructions."
        ),
    )


def render_sentiment_report(report: SentimentReport) -> str:
    """Render the typed fields ahead of the analyst narrative."""
    return "\n".join(
        [
            f"**Overall Sentiment:** **{report.overall_band.value}** "
            f"(Score: {report.overall_score:.1f}/10)",
            f"**Confidence:** {report.confidence.capitalize()}",
            "",
            report.narrative,
        ]
    )
