"""Record how typed research artifacts were generated.

Revision ID: 0003_artifact_generation_method
Revises: 0002_research_artifacts
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_artifact_generation_method"
down_revision: str | Sequence[str] | None = "0002_research_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_artifacts",
        sa.Column(
            "generation_method",
            sa.String(length=40),
            nullable=False,
            server_default="legacy_unknown",
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("run_artifacts") as batch_op:
        batch_op.drop_column("generation_method")
