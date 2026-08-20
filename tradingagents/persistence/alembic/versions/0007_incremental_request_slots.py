"""Persist immutable Incremental request slots before execution.

Revision ID: 0007_incremental_request_slots
Revises: 0006_run_backed_full_nodes
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_incremental_request_slots"
down_revision: str | Sequence[str] | None = "0006_run_backed_full_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "full_baseline_run_id",
            sa.String(36),
        ),
    )
    op.add_column("runs", sa.Column("incremental_cutoff", sa.Date()))
    op.add_column(
        "runs", sa.Column("incremental_input_fingerprint", sa.String(64))
    )
    op.create_index(
        "uq_active_incremental_cycle_cutoff",
        "runs",
        ["full_baseline_run_id", "incremental_cutoff"],
        unique=True,
        sqlite_where=sa.text(
            "research_kind = 'incremental' AND trashed_at IS NULL "
            "AND status IN ('queued', 'running', 'succeeded')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_active_incremental_cycle_cutoff", table_name="runs")
    op.drop_column("runs", "incremental_input_fingerprint")
    op.drop_column("runs", "incremental_cutoff")
    op.drop_column("runs", "full_baseline_run_id")
