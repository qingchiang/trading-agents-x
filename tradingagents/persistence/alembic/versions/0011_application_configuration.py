"""Add application configuration without changing retained research."""

import sqlalchemy as sa
from alembic import op

revision = "0011_application_configuration"
down_revision = "0010_decision_confidence_levels"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "application_configuration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("values_json", sa.JSON(), nullable=False),
        sa.Column("initialized", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "configuration_credentials",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("configuration_credentials")
    op.drop_table("application_configuration")
