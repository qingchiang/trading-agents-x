"""Independent runtime baseline; predecessor conversion is explicitly offline."""

from importlib import resources

from alembic import op

revision = "0100_independent"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = resources.files("tradingagents.persistence").joinpath("alembic/baseline.sql").read_text()
    for statement in sql.split(";"):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement)


def downgrade():
    raise RuntimeError("Restore the original database and old program to roll back the cutover")
