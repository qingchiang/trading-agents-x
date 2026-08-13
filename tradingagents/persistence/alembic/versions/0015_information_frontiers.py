"""Persist Research Execution and Revision Information Frontiers.

Revision ID: 0015_information_frontiers
Revises: 0014_research_review_audit_fixes
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_information_frontiers"
down_revision: str | Sequence[str] | None = "0014_research_review_audit_fixes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_revision_triggers(*, include_frontier: bool) -> None:
    frontier = "information_frontier, " if include_frontier else ""
    op.execute(
        f"""
        CREATE TRIGGER research_revisions_immutable_content
        BEFORE UPDATE OF chain_id, sequence, predecessor_revision_id, cutoff,
          {frontier}role, execution_strategy, outcome, change_conclusion,
          indeterminate_reason, language, current_state_json, delta_json,
          coverage_json, update_summary_json, evidence_snapshot_json,
          research_update_audit_json, metrics_json, created_at
        ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revision content is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER research_revisions_no_delete
        BEFORE DELETE ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revisions cannot be deleted');
        END
        """
    )


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute("DROP TRIGGER IF EXISTS research_revisions_no_delete")
    op.add_column("runs", sa.Column("information_frontier", sa.String(64), nullable=True))
    op.add_column(
        "research_revisions",
        sa.Column("information_frontier", sa.String(64), nullable=True),
    )
    _create_revision_triggers(include_frontier=True)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute("DROP TRIGGER IF EXISTS research_revisions_no_delete")
    op.drop_column("research_revisions", "information_frontier")
    op.drop_column("runs", "information_frontier")
    _create_revision_triggers(include_frontier=False)
