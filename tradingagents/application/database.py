"""SQLAlchemy models and SQLite engine configuration."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    instrument_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    instrument_local_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    research_chain_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    update_intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    research_chain_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_chains.id", ondelete="RESTRICT"), nullable=True
    )
    baseline_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    research_execution_strategy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    research_update_audit_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    attempts: Mapped[list[RunAttemptRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_runs_claim", "status", "lease_expires_at", "created_at"),
        Index("ix_runs_trash", "trashed_at", "created_at"),
        Index(
            "uq_runs_active_research_chain_update",
            "research_chain_id",
            unique=True,
            sqlite_where=text("research_chain_id IS NOT NULL AND status IN ('queued', 'running')"),
        ),
    )


class ResearchChainRecord(Base):
    __tablename__ = "research_chains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instrument: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index(
            "uq_research_chains_primary_instrument",
            "instrument",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
    )


class ResearchRevisionRecord(Base):
    __tablename__ = "research_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chain_id: Mapped[str] = mapped_column(
        ForeignKey("research_chains.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    producing_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    cutoff: Mapped[date] = mapped_column(Date, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    legacy_outcome: Mapped[str] = mapped_column(
        "outcome", String(30), nullable=False, default="not_applicable"
    )
    change_conclusion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    indeterminate_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language: Mapped[str] = mapped_column(String(40), nullable=False)
    current_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    delta_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    update_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    research_update_audit_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("chain_id", "sequence"),
        Index("ix_research_revisions_chain_order", "chain_id", "sequence"),
    )


class RunAttemptRecord(Base):
    __tablename__ = "run_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    checkpoint_thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[RunRecord] = relationship(back_populates="attempts")

    __table_args__ = (UniqueConstraint("run_id", "attempt"),)


class RunEventRecord(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    node: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index("ix_run_events_replay", "run_id", "sequence"),
    )


class RunArtifactRecord(Base):
    __tablename__ = "run_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    generation_method: Mapped[str] = mapped_column(String(40), nullable=False)
    generation_observations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage",
            "role",
            "round",
            "prompt_version",
            name="uq_run_artifact_identity",
        ),
        Index(
            "ix_run_artifacts_order",
            "run_id",
            "attempt",
            "created_at",
        ),
    )


class RunEvidenceRecord(Base):
    __tablename__ = "run_evidence"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    sealed_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    market: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    analysis_date: Mapped[date] = mapped_column(Date, nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    numeric_audit_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OutcomeRecord(Base):
    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    research_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_revisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    benchmark: Mapped[str] = mapped_column(String(64), nullable=False)
    market_timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    method_category: Mapped[str] = mapped_column(String(80), nullable=False)
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    price_semantics: Mapped[str] = mapped_column(String(80), nullable=False)
    adjustment_semantics: Mapped[str] = mapped_column(String(80), nullable=False)
    horizon_limit: Mapped[str] = mapped_column(Text, nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    observation_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    observation_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    holding_intervals: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    raw_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_outcomes_due", "status", "next_check_at"),)


class ReflectionRecord(Base):
    __tablename__ = "reflections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outcome_id: Mapped[int] = mapped_column(
        ForeignKey("outcomes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    current_generation_cycle_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    successful_attempt_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )


class ReflectionGenerationCycleRecord(Base):
    __tablename__ = "reflection_generation_cycles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    outcome_id: Mapped[int] = mapped_column(
        ForeignKey("outcomes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_reflection_generation_cycle_active_outcome",
            "outcome_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )


class ReflectionAttemptRecord(Base):
    __tablename__ = "reflection_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reflection_id: Mapped[int] = mapped_column(
        ForeignKey("reflections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("reflection_generation_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    diagnostics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage_status: Mapped[str] = mapped_column(String(20), nullable=False)
    llm_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_miss_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wall_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_reported_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalid_candidate: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalid_candidate_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invalid_candidate_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_issues_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("generation_cycle_id", "sequence", name="uq_reflection_attempt_sequence"),
    )


class OutcomeFeedbackRecord(Base):
    __tablename__ = "outcome_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reflection_id: Mapped[int] = mapped_column(
        ForeignKey("reflections.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    qualification_policy_version: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    method_category: Mapped[str] = mapped_column(String(80), nullable=False)
    horizon_limit: Mapped[str] = mapped_column(Text, nullable=False)
    applicability_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    qualified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def create_sqlite_engine(path: Path, *, busy_timeout_ms: int = 5000) -> Engine:
    """Create an engine whose every connection enforces local SQLite policy."""
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": busy_timeout_ms / 1000},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine
