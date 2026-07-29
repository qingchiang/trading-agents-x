"""Structured schemas used by evidence-collection analysts."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class SentimentBand(str, Enum):
    """Discrete direction produced by the sentiment analyst."""

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentSourceStatus(str, Enum):
    """Whether one applicable source contains a substantive directional signal."""

    SUBSTANTIVE = "substantive"
    NO_SIGNAL = "no_signal"
    UNAVAILABLE = "unavailable"


class SentimentSourceAssessment(BaseModel):
    """Model interpretation of one locally identified sentiment source."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    status: SentimentSourceStatus
    direction: SentimentBand | None = Field(
        default=None,
        description=(
            "Directional reading for a substantive source; null when the "
            "source has no usable signal or is unavailable."
        ),
    )
    summary: NonEmptyText | None = Field(
        default=None,
        description=(
            "Concise source-level synthesis. When omitted for a substantive "
            "source, the renderer reuses its first validated key-evidence item."
        ),
    )
    key_evidence: tuple[NonEmptyText, ...] = ()
    limitations: tuple[NonEmptyText, ...] = ()

    @field_validator("direction", mode="before")
    @classmethod
    def normalize_direction(cls, value: object) -> object:
        return _normalize_sentiment_band(value)

    @model_validator(mode="after")
    def validate_signal_fields(self) -> SentimentSourceAssessment:
        if self.status is SentimentSourceStatus.SUBSTANTIVE:
            if self.direction is None:
                raise ValueError(
                    "substantive source assessments require a direction"
                )
            if not self.key_evidence:
                raise ValueError(
                    "substantive source assessments require key evidence"
                )
        elif self.direction is not None or self.key_evidence:
            raise ValueError(
                "non-substantive source assessments cannot invent a "
                "direction or key evidence"
            )
        return self


class SentimentReport(BaseModel):
    """Rich source-audited sentiment interpretation without self-rated confidence."""

    model_config = ConfigDict(extra="forbid")

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
    executive_summary: NonEmptyText = Field(
        description=(
            "Concise synthesis of the overall direction and the evidence "
            "that matters most."
        ),
    )
    source_assessments: tuple[SentimentSourceAssessment, ...] = Field(
        min_length=1,
        description=(
            "Exactly one assessment for every applicable source_id supplied "
            "by the application."
        ),
    )
    cross_source_consensus: tuple[NonEmptyText, ...] = Field(
        default=(),
        description="Points on which two or more sources agree.",
    )
    cross_source_divergences: tuple[NonEmptyText, ...] = Field(
        default=(),
        description="Material conflicts or differences between sources.",
    )
    dominant_themes: tuple[NonEmptyText, ...] = Field(
        min_length=1,
        description="Recurring narratives that dominate current sentiment.",
    )
    catalysts: tuple[NonEmptyText, ...] = Field(
        default=(),
        description="Potential sentiment catalysts; may be empty.",
    )
    risks: tuple[NonEmptyText, ...] = Field(
        min_length=1,
        description="Material risks surfaced by the sentiment evidence.",
    )
    limitations: tuple[NonEmptyText, ...] = Field(
        min_length=1,
        description="Coverage, timing, or interpretation limitations.",
    )

    @field_validator("overall_band", mode="before")
    @classmethod
    def normalize_overall_band(cls, value: object) -> object:
        return _normalize_sentiment_band(value)

    @model_validator(mode="after")
    def validate_source_ids(self) -> SentimentReport:
        source_ids = [
            assessment.source_id for assessment in self.source_assessments
        ]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sentiment source_id values must be unique")
        return self


def _normalize_sentiment_band(value: object) -> object:
    """Accept enum casing differences without broadening the fixed vocabulary."""

    if value is None or isinstance(value, SentimentBand):
        return value
    normalized = " ".join(str(value).strip().split()).casefold()
    for band in SentimentBand:
        if band.value.casefold() == normalized:
            return band
    return value


def validate_sentiment_sources(
    report: SentimentReport,
    expected_statuses: Mapping[str, SentimentSourceStatus],
) -> SentimentReport:
    """Reject omitted, invented, duplicated, or status-shifted source outputs."""

    actual = {
        assessment.source_id: assessment
        for assessment in report.source_assessments
    }
    expected_ids = set(expected_statuses)
    actual_ids = set(actual)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unknown = sorted(actual_ids - expected_ids)
        raise ValueError(
            "sentiment source assessment mismatch: "
            f"missing={missing}, unknown={unknown}"
        )
    mismatched = sorted(
        source_id
        for source_id, status in expected_statuses.items()
        if actual[source_id].status is not status
    )
    if mismatched:
        raise ValueError(
            "sentiment source status mismatch: "
            + ", ".join(mismatched)
        )
    return report


def render_sentiment_report(
    report: SentimentReport,
    *,
    confidence: Literal["low", "medium", "high"],
    confidence_score: Literal[0.25, 0.55, 0.8],
    source_labels: Mapping[str, str],
) -> str:
    """Render the complete typed report with a local, stable Markdown layout."""

    def table_cell(value: str) -> str:
        return " ".join(value.replace("|", r"\|").split())

    source_rows = []
    for assessment in report.source_assessments:
        label = source_labels[assessment.source_id]
        direction = (
            assessment.direction.value
            if assessment.direction is not None
            else "—"
        )
        key_evidence = (
            "; ".join(assessment.key_evidence)
            if assessment.key_evidence
            else "—"
        )
        summary = (
            assessment.summary
            or (
                assessment.key_evidence[0]
                if assessment.key_evidence
                else "—"
            )
        )
        limitations = (
            "; ".join(assessment.limitations)
            if assessment.limitations
            else "—"
        )
        source_rows.append(
            "| "
            + " | ".join(
                (
                    f"{table_cell(label)} (`{assessment.source_id}`)",
                    assessment.status.value,
                    direction,
                    table_cell(summary),
                    table_cell(key_evidence),
                    table_cell(limitations),
                )
            )
            + " |"
        )

    def section(title: str, items: tuple[str, ...]) -> list[str]:
        return [
            f"## {title}",
            *([f"- {item}" for item in items] if items else ["- —"]),
        ]

    return "\n".join(
        [
            f"**Overall Sentiment:** **{report.overall_band.value}** "
            f"(Score: {report.overall_score:.1f}/10)",
            f"**Confidence:** {confidence.capitalize()} "
            f"({confidence_score:.2f})",
            "",
            "## Executive Summary",
            report.executive_summary,
            "",
            "## Source Assessments",
            (
                "| Source | Status | Direction | Assessment | "
                "Key Evidence | Limitations |"
            ),
            "|---|---|---|---|---|---|",
            *source_rows,
            "",
            *section(
                "Cross-source Consensus",
                report.cross_source_consensus,
            ),
            "",
            *section(
                "Cross-source Divergences",
                report.cross_source_divergences,
            ),
            "",
            *section("Dominant Themes", report.dominant_themes),
            "",
            *section("Catalysts", report.catalysts),
            "",
            *section("Risks", report.risks),
            "",
            *section("Limitations", report.limitations),
            "",
        ]
    ).rstrip()
