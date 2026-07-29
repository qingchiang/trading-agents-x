"""Rename the recoverable archive lifecycle to trash.

Revision ID: 0003_trash_lifecycle
Revises: 0002_outcome_schedule
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_trash_lifecycle"
down_revision: str | Sequence[str] | None = "0002_outcome_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_runs_archive", table_name="runs")
    op.alter_column(
        "runs",
        "archived_at",
        new_column_name="trashed_at",
    )
    op.create_index(
        "ix_runs_trash",
        "runs",
        ["trashed_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runs_trash", table_name="runs")
    op.alter_column(
        "runs",
        "trashed_at",
        new_column_name="archived_at",
    )
    op.create_index(
        "ix_runs_archive",
        "runs",
        ["archived_at", "created_at"],
        unique=False,
    )
