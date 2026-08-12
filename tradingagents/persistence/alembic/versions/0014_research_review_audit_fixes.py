"""Repair legacy retry claims and identify the Attempt envelope.

Revision ID: 0014_research_review_audit_fixes
Revises: 0013_retire_qualified_feedback
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_research_review_audit_fixes"
down_revision: str | Sequence[str] | None = "0013_retire_qualified_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reflection_attempts") as batch:
        batch.add_column(
            sa.Column(
                "attempt_schema_version",
                sa.String(80),
                nullable=False,
                server_default="outcome_reflection_attempt.v1",
            )
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO reflection_generation_cycles "
            "(id, outcome_id, status, trigger, origin, retry_ordinal, queued_at, due_at) "
            "SELECT 'legacy-retry-' || reflections.id, reflections.outcome_id, "
            "'queued', 'legacy_retry_schedule', 'automatic', 1, "
            "COALESCE(reflections.last_attempted_at, reflections.created_at), "
            "reflections.next_retry_at FROM reflections "
            "WHERE reflections.status = 'retryable_failure' "
            "AND reflections.next_retry_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM reflection_generation_cycles active "
            "WHERE active.outcome_id = reflections.outcome_id "
            "AND active.status IN ('queued', 'running'))"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE reflections SET current_generation_cycle_id = "
            "'legacy-retry-' || reflections.id "
            "WHERE reflections.status = 'retryable_failure' "
            "AND reflections.next_retry_at IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM reflection_generation_cycles cycle "
            "WHERE cycle.id = 'legacy-retry-' || reflections.id)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE reflections SET current_generation_cycle_id = "
            "'legacy-reflection-' || reflections.id "
            "WHERE current_generation_cycle_id = 'legacy-retry-' || reflections.id "
            "AND NOT EXISTS (SELECT 1 FROM reflection_attempts attempt "
            "WHERE attempt.generation_cycle_id = reflections.current_generation_cycle_id)"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM reflection_generation_cycles "
            "WHERE id LIKE 'legacy-retry-%' AND trigger = 'legacy_retry_schedule' "
            "AND NOT EXISTS (SELECT 1 FROM reflection_attempts attempt "
            "WHERE attempt.generation_cycle_id = reflection_generation_cycles.id)"
        )
    )
    with op.batch_alter_table("reflection_attempts") as batch:
        batch.drop_column("attempt_schema_version")
