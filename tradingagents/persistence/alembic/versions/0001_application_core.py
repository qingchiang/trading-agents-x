"""Create the local run-center schema.

Revision ID: 0001_application_core
Revises:
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_application_core"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("instrument_name", sa.String(length=300), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("current_attempt", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_runs_status", "runs", ["status"], unique=False)
    op.create_index(
        "ix_runs_claim",
        "runs",
        ["status", "lease_expires_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_runs_archive",
        "runs",
        ["archived_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("checkpoint_thread_id", sa.String(length=200), nullable=False),
        sa.Column("resume_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt"),
    )
    op.create_index(
        "ix_run_attempts_run_id", "run_attempts", ["run_id"], unique=False
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("node", sa.String(length=160), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    op.create_index(
        "ix_run_events_run_id", "run_events", ["run_id"], unique=False
    )
    op.create_index(
        "ix_run_events_replay",
        "run_events",
        ["run_id", "sequence"],
        unique=False,
    )
    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("generation_method", sa.String(length=40), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "role",
            "round",
            "content_hash",
            name="uq_run_artifact_identity",
        ),
    )
    op.create_index(
        "ix_run_artifacts_run_id",
        "run_artifacts",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_run_artifacts_order",
        "run_artifacts",
        ["run_id", "attempt", "created_at"],
        unique=False,
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("structured_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "name"),
    )
    op.create_index("ix_reports_run_id", "reports", ["run_id"], unique=False)
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=80), nullable=True),
        sa.Column("asset_type", sa.String(length=20), nullable=False),
        sa.Column("analysis_date", sa.Date(), nullable=False),
        sa.Column("rating", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("decision_json", sa.JSON(), nullable=False),
        sa.Column("evidence_bundle_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_decisions_run_id", "decisions", ["run_id"], unique=True
    )
    op.create_index(
        "ix_decisions_ticker", "decisions", ["ticker"], unique=False
    )
    op.create_index(
        "ix_decisions_market", "decisions", ["market"], unique=False
    )
    op.create_table(
        "outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("benchmark", sa.String(length=64), nullable=False),
        sa.Column("observation_start", sa.Date(), nullable=True),
        sa.Column("observation_end", sa.Date(), nullable=True),
        sa.Column("holding_intervals", sa.Integer(), nullable=False),
        sa.Column("raw_return", sa.Float(), nullable=True),
        sa.Column("alpha_return", sa.Float(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id"),
    )
    op.create_index(
        "ix_outcomes_decision_id", "outcomes", ["decision_id"], unique=True
    )
    op.create_index(
        "ix_outcomes_status", "outcomes", ["status"], unique=False
    )
    op.create_table(
        "reflections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("outcome_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["outcome_id"], ["outcomes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outcome_id"),
    )
    op.create_index(
        "ix_reflections_outcome_id",
        "reflections",
        ["outcome_id"],
        unique=True,
    )
    op.create_table(
        "legacy_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash"),
    )


def downgrade() -> None:
    op.drop_table("legacy_imports")
    op.drop_index("ix_reflections_outcome_id", table_name="reflections")
    op.drop_table("reflections")
    op.drop_index("ix_outcomes_status", table_name="outcomes")
    op.drop_index("ix_outcomes_decision_id", table_name="outcomes")
    op.drop_table("outcomes")
    op.drop_index("ix_decisions_market", table_name="decisions")
    op.drop_index("ix_decisions_ticker", table_name="decisions")
    op.drop_index("ix_decisions_run_id", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_reports_run_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_run_artifacts_order", table_name="run_artifacts")
    op.drop_index("ix_run_artifacts_run_id", table_name="run_artifacts")
    op.drop_table("run_artifacts")
    op.drop_index("ix_run_events_replay", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_run_attempts_run_id", table_name="run_attempts")
    op.drop_table("run_attempts")
    op.drop_index("ix_runs_claim", table_name="runs")
    op.drop_index("ix_runs_archive", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_table("runs")
