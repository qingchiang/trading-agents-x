"""Remove the retired legacy review tables.

Revision ID: 0005_remove_legacy_memory
Revises: 0004_instrument_local_name
Create Date: 2026-08-20

The compatibility break intentionally discards Outcome and Reflection rows.
Runs and their execution-history tables are independent and remain intact.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_remove_legacy_memory"
down_revision: str | Sequence[str] | None = "0004_instrument_local_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the child index/table before the parent so SQLite foreign-key mode
    # remains enabled for the real application migration.
    op.drop_index("ix_reflections_outcome_id", table_name="reflections")
    op.drop_table("reflections")
    op.drop_index("ix_outcomes_due", table_name="outcomes")
    op.drop_index("ix_outcomes_status", table_name="outcomes")
    op.drop_index("ix_outcomes_decision_id", table_name="outcomes")
    op.drop_table("outcomes")


def downgrade() -> None:
    # Downgrade restores the retired schema empty; discarded review data is
    # deliberately not fabricated or recoverable through this migration.
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
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
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
    op.create_index("ix_outcomes_status", "outcomes", ["status"], unique=False)
    op.create_index(
        "ix_outcomes_due", "outcomes", ["status", "next_check_at"], unique=False
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
        "ix_reflections_outcome_id", "reflections", ["outcome_id"], unique=True
    )
