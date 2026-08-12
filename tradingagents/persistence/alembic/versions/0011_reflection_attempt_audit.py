"""Add immutable Outcome Reflection generation-cycle and Attempt audit rows.

Revision ID: 0011_reflection_attempt_audit
Revises: 0010_outcome_feedback_policy
Create Date: 2026-08-11
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_reflection_attempt_audit"
down_revision: str | Sequence[str] | None = "0010_outcome_feedback_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_generation_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("outcome_id", sa.Integer(), sa.ForeignKey("outcomes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("retry_ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_reflection_generation_cycles_outcome_id", "reflection_generation_cycles", ["outcome_id"])
    op.create_index("ix_reflection_generation_cycles_status", "reflection_generation_cycles", ["status"])
    op.create_index(
        "uq_reflection_generation_cycle_active_outcome",
        "reflection_generation_cycles",
        ["outcome_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "reflection_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reflection_id", sa.Integer(), sa.ForeignKey("reflections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation_cycle_id", sa.String(36), sa.ForeignKey("reflection_generation_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("attempt_kind", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=True),
        sa.Column("schema_version", sa.String(80), nullable=True),
        sa.Column("diagnostics_json", sa.JSON(), nullable=True),
        sa.Column("usage_status", sa.String(20), nullable=False),
        sa.Column("llm_calls", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_hit_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_miss_input_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_output_tokens", sa.Integer(), nullable=True),
        sa.Column("wall_time_seconds", sa.Float(), nullable=True),
        sa.Column("provider_reported_cost_usd", sa.Float(), nullable=True),
        sa.Column("invalid_candidate", sa.Text(), nullable=True),
        sa.Column("invalid_candidate_digest", sa.String(64), nullable=True),
        sa.Column("invalid_candidate_length", sa.Integer(), nullable=True),
        sa.Column("validation_issues_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint("generation_cycle_id", "sequence", name="uq_reflection_attempt_sequence"),
    )
    op.create_index("ix_reflection_attempts_reflection_id", "reflection_attempts", ["reflection_id"])
    op.create_index("ix_reflection_attempts_generation_cycle_id", "reflection_attempts", ["generation_cycle_id"])
    with op.batch_alter_table("reflections") as batch:
        batch.add_column(sa.Column("current_generation_cycle_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("successful_attempt_id", sa.Integer(), nullable=True))
        batch.create_index("ix_reflections_current_generation_cycle_id", ["current_generation_cycle_id"])
        batch.create_index("ix_reflections_successful_attempt_id", ["successful_attempt_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, outcome_id, status, created_at, generated_at, last_attempted_at, error_code FROM reflections")).mappings()
    for row in rows:
        if row["status"] == "pending":
            continue
        timestamp = row["generated_at"] or row["last_attempted_at"] or row["created_at"]
        cycle_id = f"legacy-reflection-{row['id']}"
        if row["status"] == "generated":
            cycle_status, attempt_outcome, issues, diagnostics = "succeeded", "generated", None, None
        elif row["status"] == "invalid":
            cycle_status, attempt_outcome = "invalid", "invalid"
            issues = ["legacy_unknown_invalid_reason"]
            diagnostics = {"code": "legacy_unknown_invalid_reason"}
        else:
            cycle_status, attempt_outcome = "failed", "provider_failure"
            code = (row["error_code"] or "legacy_unknown_retryable_error")[:80]
            issues, diagnostics = None, {"error_code": code}
        bind.execute(
            sa.text(
                "INSERT INTO reflection_generation_cycles "
                "(id, outcome_id, status, trigger, origin, retry_ordinal, queued_at, started_at, finished_at) "
                "VALUES (:id, :outcome_id, :status, 'legacy_migration', 'legacy', 0, :at, :at, :at)"
            ),
            {"id": cycle_id, "outcome_id": row["outcome_id"], "status": cycle_status, "at": timestamp},
        )
        attempt_id = bind.execute(
            sa.text(
                "INSERT INTO reflection_attempts "
                "(reflection_id, generation_cycle_id, sequence, trigger, origin, attempt_kind, started_at, finished_at, outcome, schema_version, diagnostics_json, usage_status, validation_issues_json) "
                "VALUES (:reflection_id, :cycle_id, 1, 'legacy_migration', 'legacy', 'legacy_unstructured', :at, :at, :outcome, 'legacy_unstructured.v1', :diagnostics, 'legacy_unknown', :issues)"
            ),
            {"reflection_id": row["id"], "cycle_id": cycle_id, "at": timestamp, "outcome": attempt_outcome, "diagnostics": json.dumps(diagnostics) if diagnostics else None, "issues": json.dumps(issues) if issues else None},
        ).lastrowid
        bind.execute(
            sa.text(
                "UPDATE reflections SET current_generation_cycle_id = :cycle_id, successful_attempt_id = :attempt_id WHERE id = :reflection_id"
            ),
            {"cycle_id": cycle_id, "attempt_id": attempt_id if attempt_outcome == "generated" else None, "reflection_id": row["id"]},
        )


def downgrade() -> None:
    with op.batch_alter_table("reflections") as batch:
        batch.drop_index("ix_reflections_successful_attempt_id")
        batch.drop_index("ix_reflections_current_generation_cycle_id")
        batch.drop_column("successful_attempt_id")
        batch.drop_column("current_generation_cycle_id")
    op.drop_table("reflection_attempts")
    op.drop_table("reflection_generation_cycles")
