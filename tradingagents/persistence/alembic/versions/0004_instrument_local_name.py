"""Persist point-in-time-safe market-local instrument names.

Revision ID: 0004_instrument_local_name
Revises: 0003_artifact_generation_observations
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_instrument_local_name"
down_revision: str | Sequence[str] | None = (
    "0003_artifact_generation_observations"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("instrument_local_name", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "instrument_local_name")
