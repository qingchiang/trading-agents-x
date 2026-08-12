"""Record auditable Outcome Feedback retirement reasons.

Revision ID: 0013_retire_qualified_feedback
Revises: 0012_operate_reflection_cycles
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_retire_qualified_feedback"
down_revision: str | Sequence[str] | None = "0012_operate_reflection_cycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("outcome_feedback") as batch:
        batch.add_column(sa.Column("retirement_reason", sa.String(30), nullable=True))
        batch.add_column(sa.Column("retirement_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outcome_feedback") as batch:
        batch.drop_column("retirement_note")
        batch.drop_column("retirement_reason")
