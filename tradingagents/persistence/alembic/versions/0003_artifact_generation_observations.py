"""Persist per-component structured-generation observations.

Revision ID: 0003_artifact_generation_observations
Revises: 0002_remove_legacy_imports
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_artifact_generation_observations"
down_revision: str | Sequence[str] | None = "0002_remove_legacy_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_artifacts",
        sa.Column("generation_observations_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("run_artifacts") as batch_op:
        batch_op.drop_column("generation_observations_json")
