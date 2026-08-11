"""Operate bounded, idempotent Outcome Reflection generation cycles.

Revision ID: 0012_operate_reflection_cycles
Revises: 0011_reflection_attempt_audit
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_operate_reflection_cycles"
down_revision: str | Sequence[str] | None = "0011_reflection_attempt_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reflection_generation_cycles",
        sa.Column("idempotency_key", sa.String(200), nullable=True),
    )
    op.add_column(
        "reflection_generation_cycles", sa.Column("due_at", sa.DateTime(), nullable=True)
    )
    op.create_index(
        "uq_reflection_generation_cycle_idempotency",
        "reflection_generation_cycles",
        ["outcome_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    with op.batch_alter_table("reflection_generation_cycles") as batch:
        batch.drop_index("uq_reflection_generation_cycle_idempotency")
        batch.drop_column("due_at")
        batch.drop_column("idempotency_key")
