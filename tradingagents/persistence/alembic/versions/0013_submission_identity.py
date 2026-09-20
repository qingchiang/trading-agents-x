"""Retain submission identity and repair legacy connection reset baselines."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0013_submission_identity"
down_revision = "0012_model_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("submission_json", sa.JSON(), nullable=True))
    bind = op.get_bind()
    changed = False
    for identity, raw in bind.execute(
        sa.text("SELECT id, definition FROM model_connections WHERE legacy_provider IS NOT NULL")
    ).all():
        definition = json.loads(raw)
        if definition.get("deleted") or definition.get("template_origin"):
            continue
        definition["template"] = {
            key: definition[key]
            for key in (
                "transport",
                "compatibility",
                "discovery",
                "key_required",
                "reasoning_defaults",
            )
        }
        definition["template_origin"] = "upgrade"
        bind.execute(
            sa.text("UPDATE model_connections SET definition=:value WHERE id=:id"),
            {"value": json.dumps(definition), "id": identity},
        )
        changed = True
    if changed:
        bind.execute(
            sa.text(
                "UPDATE application_configuration SET revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE id=1"
            )
        )


def downgrade():
    op.drop_column("runs", "submission_json")
