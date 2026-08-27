"""Add immutable Full-Run metadata and same-identity Timeline node roles.

Revision ID: 0006_run_backed_full_nodes
Revises: 0005_remove_legacy_memory
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_run_backed_full_nodes"
down_revision: str | Sequence[str] | None = "0005_remove_legacy_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("research_schema_version", sa.String(20)))
    op.add_column("runs", sa.Column("information_cutoff_at", sa.DateTime()))
    op.add_column("runs", sa.Column("method_snapshot_json", sa.JSON()))
    op.add_column("runs", sa.Column("research_kind", sa.String(20)))
    op.create_table(
        "research_nodes",
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("research_kind", sa.String(20), nullable=False),
        sa.Column("full_baseline_run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["full_baseline_run_id"], ["runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "primary_research_cycles",
        sa.Column("instrument", sa.String(64), nullable=False),
        sa.Column("full_run_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["full_run_id"], ["research_nodes.run_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("instrument"),
    )


def downgrade() -> None:
    op.drop_table("primary_research_cycles")
    op.drop_table("research_nodes")
    op.drop_column("runs", "research_kind")
    op.drop_column("runs", "method_snapshot_json")
    op.drop_column("runs", "information_cutoff_at")
    op.drop_column("runs", "research_schema_version")
