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
    # Earlier APIs could Trash a Full without its children and leave Primary
    # pointing at that inactive Full. Do not invent a replacement choice:
    # cascade only children that are still active, preserve earlier independent
    # child Trash, and leave Primary empty for an explicit user selection.
    op.execute(
        sa.text(
            """
            UPDATE runs AS child
            SET trashed_at = (
                    SELECT full_run.trashed_at
                    FROM research_nodes AS child_node
                    JOIN runs AS full_run
                      ON full_run.id = child_node.full_baseline_run_id
                    WHERE child_node.run_id = child.id
                ),
                trash_cascade_full_run_id = (
                    SELECT child_node.full_baseline_run_id
                    FROM research_nodes AS child_node
                    WHERE child_node.run_id = child.id
                ),
                updated_at = (
                    SELECT full_run.updated_at
                    FROM research_nodes AS child_node
                    JOIN runs AS full_run
                      ON full_run.id = child_node.full_baseline_run_id
                    WHERE child_node.run_id = child.id
                )
            WHERE child.trashed_at IS NULL
              AND child.id IN (
                  SELECT child_node.run_id
                  FROM research_nodes AS child_node
                  JOIN runs AS full_run
                    ON full_run.id = child_node.full_baseline_run_id
                  WHERE child_node.research_kind = 'incremental'
                    AND full_run.trashed_at IS NOT NULL
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM primary_research_cycles
            WHERE full_run_id IN (
                SELECT node.run_id
                FROM research_nodes AS node
                JOIN runs AS full_run ON full_run.id = node.run_id
                WHERE node.research_kind = 'full'
                  AND full_run.trashed_at IS NOT NULL
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_runs_trash_cascade_full_run_id", table_name="runs")
    op.drop_column("runs", "trash_cascade_full_run_id")
