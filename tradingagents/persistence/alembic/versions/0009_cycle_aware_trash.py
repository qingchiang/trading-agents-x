"""Record Full-Cycle Trash cascade ownership.

Revision ID: 0009_cycle_aware_trash
Revises: 0008_incremental_node_products
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_cycle_aware_trash"
down_revision: str | Sequence[str] | None = "0008_incremental_node_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("trash_cascade_full_run_id", sa.String(36)),
    )
    op.create_index(
        "ix_runs_trash_cascade_full_run_id",
        "runs",
        ["trash_cascade_full_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_trash_cascade_full_run_id", table_name="runs")
    op.drop_column("runs", "trash_cascade_full_run_id")
