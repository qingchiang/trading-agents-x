"""Add initial Research Chain and immutable Revision persistence.

Revision ID: 0005_research_chains
Revises: 0004_instrument_local_name
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_research_chains"
down_revision: str | Sequence[str] | None = "0004_instrument_local_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "research_chain_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "research_chains",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("instrument", sa.String(length=64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_chains_instrument",
        "research_chains",
        ["instrument"],
        unique=False,
    )
    op.create_index(
        "uq_research_chains_primary_instrument",
        "research_chains",
        ["instrument"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
    )
    op.create_table(
        "research_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chain_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("predecessor_revision_id", sa.String(length=36), nullable=True),
        sa.Column("producing_run_id", sa.String(length=36), nullable=True),
        sa.Column("cutoff", sa.Date(), nullable=False),
        sa.Column("execution_strategy", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("language", sa.String(length=40), nullable=False),
        sa.Column("current_state_json", sa.JSON(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("update_summary_json", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["research_chains.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_revision_id"],
            ["research_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["producing_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chain_id", "sequence"),
        sa.UniqueConstraint("producing_run_id"),
    )
    op.create_index(
        "ix_research_revisions_chain_id",
        "research_revisions",
        ["chain_id"],
        unique=False,
    )
    op.create_index(
        "ix_research_revisions_chain_order",
        "research_revisions",
        ["chain_id", "sequence"],
        unique=False,
    )
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
    op.execute(
        """
        CREATE TRIGGER research_revisions_no_delete
        BEFORE DELETE ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revisions cannot be deleted');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.drop_index("ix_research_revisions_chain_order", table_name="research_revisions")
    op.drop_index("ix_research_revisions_chain_id", table_name="research_revisions")
    op.drop_table("research_revisions")
    op.drop_index("uq_research_chains_primary_instrument", table_name="research_chains")
    op.drop_index("ix_research_chains_instrument", table_name="research_chains")
    op.drop_table("research_chains")
    op.drop_column("runs", "research_chain_requested")
