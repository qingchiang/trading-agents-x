"""Artifacts contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import Field

from tradingagents.domain.common import ArtifactGenerationMethod, FrozenModel
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.reports import (
    AnalystReport,
    DebateAgenda,
    DecisionBrief,
    JudgeDraft,
    RebuttalReview,
    ResearchCase,
    RiskReview,
)

ResearchArtifactContent = (
    AnalystReport
    | DecisionBrief
    | ResearchCase
    | DebateAgenda
    | RebuttalReview
    | JudgeDraft
    | RiskReview
    | ResearchDecision
)


class ArtifactGenerationObservation(FrozenModel):
    """One structured-generation path used to produce an artifact component."""

    node: str = Field(min_length=1, max_length=160)
    task_kind: Literal["semantic_structured", "schema_serialization"]
    client_role: Literal[
        "quick_reasoning",
        "deep_reasoning",
        "quick_serializer",
        "deep_serializer",
    ]
    generation_method: ArtifactGenerationMethod


def _artifact_content_type(content: ResearchArtifactContent) -> str:
    if isinstance(content, AnalystReport):
        return "analyst_report"
    if isinstance(content, DecisionBrief):
        return "decision_brief"
    if isinstance(content, ResearchCase):
        return "research_case"
    if isinstance(content, DebateAgenda):
        return "debate_agenda"
    if isinstance(content, RebuttalReview):
        return "rebuttal_review"
    if isinstance(content, JudgeDraft):
        return "judge_draft"
    if isinstance(content, RiskReview):
        return "risk_review"
    if isinstance(content, ResearchDecision):
        return "research_decision"
    raise TypeError(f"unsupported research artifact: {type(content)!r}")


class ResearchArtifactDraft(FrozenModel):
    """Typed graph output awaiting application-owned persistence metadata."""

    node: str = Field(min_length=1, max_length=160)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["3"] = "3"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    generation_observations: tuple[ArtifactGenerationObservation, ...] = ()
    content: ResearchArtifactContent

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.content.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ResearchArtifact(FrozenModel):
    """Durable, typed output from one visible research stage."""

    id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    attempt: int = Field(ge=1)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["3"] = "3"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    generation_observations: tuple[ArtifactGenerationObservation, ...] = ()
    content: ResearchArtifactContent
    created_at: datetime

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)
