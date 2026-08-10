"""Version Outcome Feedback qualification prospectively.

Revision ID: 0010_outcome_feedback_policy
Revises: 0009_outcome_feedback_lifecycle
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_outcome_feedback_policy"
down_revision: str | Sequence[str] | None = "0009_outcome_feedback_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("outcome_feedback") as batch:
        batch.add_column(
            sa.Column("qualification_policy_version", sa.String(80), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("outcome_feedback") as batch:
        batch.drop_column("qualification_policy_version")
