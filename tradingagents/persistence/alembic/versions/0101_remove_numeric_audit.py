"""Retire numeric audit and migrate retained research references once."""

import hashlib
import json

from alembic import op
from sqlalchemy import text

revision = "0101_remove_numeric_audit"
down_revision = "0100_independent"
branch_labels = None
depends_on = None


def _decision(raw):
    value = json.loads(raw)
    for key in ("valuation_assessment", "calculation_records", "numeric_audit_status"):
        value.pop(key, None)
    for scenario in value.get("scenarios", []):
        for reference in scenario.get("reference_ranges", []):
            for endpoint in ("low", "high"):
                reference[endpoint].pop("calculation_id", None)
    for reference in value.get("market_reference_levels", []):
        reference.pop("calculation_ids", None)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def upgrade():
    connection = op.get_bind()
    if connection.scalar(text("SELECT count(*) FROM runs WHERE status IN ('queued', 'running')")):
        raise RuntimeError("Stop active research before removing numeric audit")
    for table in ("checkpoints", "writes"):
        exists = connection.scalar(
            text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table},
        )
        if exists and connection.scalar(text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("Resolve retained checkpoints before removing numeric audit")
    for row in connection.execute(text("SELECT id, decision_json FROM decisions")).mappings().all():
        connection.execute(
            text("UPDATE decisions SET decision_json=:value WHERE id=:id"),
            {"id": row["id"], "value": _decision(row["decision_json"])},
        )
    for row in (
        connection.execute(
            text(
                "SELECT id, content_json FROM run_artifacts WHERE content_type='research_decision'"
            )
        )
        .mappings()
        .all()
    ):
        value = _decision(row["content_json"])
        connection.execute(
            text("UPDATE run_artifacts SET content_json=:value, content_hash=:hash WHERE id=:id"),
            {"id": row["id"], "value": value, "hash": hashlib.sha256(value.encode()).hexdigest()},
        )
    connection.execute(
        text("UPDATE runs SET research_schema_version='3' WHERE research_schema_version='2'")
    )
    connection.execute(text("UPDATE run_artifacts SET schema_version='3' WHERE schema_version='2'"))
    # SQLite supports transactional DROP COLUMN; avoid rebuilding referenced tables.
    connection.exec_driver_sql("ALTER TABLE decisions DROP COLUMN numeric_audit_json")


def downgrade():
    raise RuntimeError("Restore the pre-migration backup and old program to restore numeric audit")
