"""Persist Shadow incremental-update findings.

Revision ID: 0007_shadow_research_updates
Revises: 0006_full_chain_updates
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_shadow_research_updates"
down_revision: str | Sequence[str] | None = "0006_full_chain_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_run_reference_triggers(*, incremental: bool) -> None:
    allowed = "NOT IN ('full', 'incremental')" if incremental else "!= 'full'"
    operations = (
        ("INSERT", "insert"),
        (
            "UPDATE OF update_intent_id, research_chain_id, "
            "baseline_revision_id, research_execution_strategy",
            "update",
        ),
    )
    for operation, suffix in operations:
        op.execute(
            f"""
            CREATE TRIGGER runs_research_update_refs_{suffix}
            BEFORE {operation} ON runs
            WHEN (NEW.update_intent_id IS NOT NULL
              OR NEW.research_chain_id IS NOT NULL
              OR NEW.baseline_revision_id IS NOT NULL
              OR NEW.research_execution_strategy IS NOT NULL) AND (
              NEW.update_intent_id IS NULL
              OR NEW.research_chain_id IS NULL
              OR NEW.baseline_revision_id IS NULL
              OR NEW.research_execution_strategy {allowed}
              OR NOT EXISTS (SELECT 1 FROM research_chains WHERE id = NEW.research_chain_id)
              OR NOT EXISTS (
                SELECT 1 FROM research_revisions
                WHERE id = NEW.baseline_revision_id
                  AND chain_id = NEW.research_chain_id
              )
            )
            BEGIN
              SELECT RAISE(ABORT, 'invalid Research Chain update references');
            END
            """
        )


def _create_revision_immutability_trigger(*, audit: bool) -> None:
    audit_column = "research_update_audit_json, " if audit else ""
    op.execute(
        f"""
        CREATE TRIGGER research_revisions_immutable_content
        BEFORE UPDATE OF chain_id, sequence, predecessor_revision_id, cutoff,
          execution_strategy, outcome, language, current_state_json, delta_json,
          coverage_json, update_summary_json, evidence_snapshot_json,
          {audit_column}metrics_json, created_at
        ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revision content is immutable');
        END
        """
    )


def upgrade() -> None:
    op.add_column("runs", sa.Column("research_update_audit_json", sa.JSON(), nullable=True))
    op.add_column(
        "research_revisions",
        sa.Column("research_update_audit_json", sa.JSON(), nullable=True),
    )
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_insert")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_update")
    _create_run_reference_triggers(incremental=True)
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    _create_revision_immutability_trigger(audit=True)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_update")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_insert")
    op.drop_column("research_revisions", "research_update_audit_json")
    op.drop_column("runs", "research_update_audit_json")
    _create_run_reference_triggers(incremental=False)
    _create_revision_immutability_trigger(audit=False)
