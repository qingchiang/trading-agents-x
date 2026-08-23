"""Persist committed Incremental Node products.

Revision ID: 0008_incremental_node_products
Revises: 0007_incremental_request_slots
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_incremental_node_products"
down_revision: str | Sequence[str] | None = "0007_incremental_request_slots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("research_nodes", sa.Column("incremental_products_json", sa.JSON()))


def downgrade() -> None:
    op.drop_column("research_nodes", "incremental_products_json")
