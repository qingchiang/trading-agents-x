"""Store typed research artifacts.

Revision ID: 0002_research_artifacts
Revises: 0001_application_core
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_research_artifacts"
down_revision: str | Sequence[str] | None = "0001_application_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "role",
            "round",
            "content_hash",
            name="uq_run_artifact_identity",
        ),
    )
    op.create_index(
        "ix_run_artifacts_run_id",
        "run_artifacts",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_run_artifacts_order",
        "run_artifacts",
        ["run_id", "attempt", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_run_artifacts_order", table_name="run_artifacts")
    op.drop_index("ix_run_artifacts_run_id", table_name="run_artifacts")
    op.drop_table("run_artifacts")
