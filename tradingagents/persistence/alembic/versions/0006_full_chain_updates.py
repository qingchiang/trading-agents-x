"""Add Full Analysis Research Chain updates.

Revision ID: 0006_full_chain_updates
Revises: 0005_research_chains
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_full_chain_updates"
down_revision: str | Sequence[str] | None = "0005_research_chains"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("update_intent_id", sa.String(36), nullable=True))
    op.add_column("runs", sa.Column("research_chain_id", sa.String(36), nullable=True))
    op.add_column("runs", sa.Column("baseline_revision_id", sa.String(36), nullable=True))
    op.add_column(
        "runs",
        sa.Column("research_execution_strategy", sa.String(20), nullable=True),
    )
    op.create_index(
        "uq_runs_update_intent_id",
        "runs",
        ["update_intent_id"],
        unique=True,
    )
    op.create_index(
        "uq_runs_active_research_chain_update",
        "runs",
        ["research_chain_id"],
        unique=True,
        sqlite_where=sa.text("research_chain_id IS NOT NULL AND status IN ('queued', 'running')"),
    )
    op.execute(
        """
        CREATE TRIGGER runs_research_update_refs_insert
        BEFORE INSERT ON runs
        WHEN (NEW.update_intent_id IS NOT NULL
          OR NEW.research_chain_id IS NOT NULL
          OR NEW.baseline_revision_id IS NOT NULL
          OR NEW.research_execution_strategy IS NOT NULL) AND (
          NEW.update_intent_id IS NULL
          OR NEW.research_chain_id IS NULL
          OR NEW.baseline_revision_id IS NULL
          OR NEW.research_execution_strategy != 'full'
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
    op.execute(
        """
        CREATE TRIGGER runs_research_update_refs_update
        BEFORE UPDATE OF update_intent_id, research_chain_id,
          baseline_revision_id, research_execution_strategy ON runs
        WHEN (NEW.update_intent_id IS NOT NULL
          OR NEW.research_chain_id IS NOT NULL
          OR NEW.baseline_revision_id IS NOT NULL
          OR NEW.research_execution_strategy IS NOT NULL) AND (
          NEW.update_intent_id IS NULL
          OR NEW.research_chain_id IS NULL
          OR NEW.baseline_revision_id IS NULL
          OR NEW.research_execution_strategy != 'full'
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
    op.add_column(
        "research_revisions",
        sa.Column(
            "delta_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(
                '\'{"schema_version":"1","opinion_changed":true,'
                '"claims":[],"questions":[],'
                '"inherited_evidence_refs":[],"new_evidence_refs":[]}\''
            ),
        ),
    )
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute(
        """
        CREATE TRIGGER research_revisions_immutable_content
        BEFORE UPDATE OF chain_id, sequence, predecessor_revision_id, cutoff,
          execution_strategy, outcome, language, current_state_json, delta_json,
          coverage_json, update_summary_json, evidence_snapshot_json,
          metrics_json, created_at
        ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revision content is immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_update")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_insert")
    op.drop_column("research_revisions", "delta_json")
    op.execute(
        """
        CREATE TRIGGER research_revisions_immutable_content
        BEFORE UPDATE OF chain_id, sequence, predecessor_revision_id, cutoff,
          execution_strategy, outcome, language, current_state_json,
          coverage_json, update_summary_json, evidence_snapshot_json,
          metrics_json, created_at
        ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revision content is immutable');
        END
        """
    )
    op.drop_index("uq_runs_active_research_chain_update", table_name="runs")
    op.drop_index("uq_runs_update_intent_id", table_name="runs")
    op.drop_column("runs", "research_execution_strategy")
    op.drop_column("runs", "baseline_revision_id")
    op.drop_column("runs", "research_chain_id")
    op.drop_column("runs", "update_intent_id")
