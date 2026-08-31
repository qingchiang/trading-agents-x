"""Replace final Decision confidence scores with rubric levels.

Revision ID: 0010_decision_confidence_levels
Revises: 0009_cycle_aware_trash
Create Date: 2026-08-31
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0010_decision_confidence_levels"
down_revision: str | Sequence[str] | None = "0009_cycle_aware_trash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _level(value: Any) -> str:
    score = float(value)
    if score < 0.50:
        return "low"
    if score < 0.80:
        return "medium"
    return "high"


def _score(value: Any) -> float:
    return {"low": 0.25, "medium": 0.65, "high": 0.90}[str(value)]


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _rewrite_json_confidence(
    table_name: str,
    id_column: str,
    json_column: str,
    converter: Any,
    *,
    hash_column: str | None = None,
    where: str | None = None,
) -> None:
    connection = op.get_bind()
    query = f"SELECT {id_column}, {json_column} FROM {table_name}"
    if where:
        query += f" WHERE {where}"
    for row_id, payload in connection.execute(sa.text(query)):
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or "confidence" not in payload:
            continue
        rewritten = dict(payload)
        rewritten["confidence"] = converter(payload["confidence"])
        assignments = f"{json_column} = :payload"
        parameters = {"payload": rewritten, "row_id": row_id}
        if hash_column is not None:
            assignments += f", {hash_column} = :content_hash"
            parameters["content_hash"] = _content_hash(rewritten)
        connection.execute(
            sa.text(
                f"UPDATE {table_name} SET {assignments} WHERE {id_column} = :row_id"
            ).bindparams(sa.bindparam("payload", type_=sa.JSON())),
            parameters,
        )


def upgrade() -> None:
    with op.batch_alter_table("decisions") as batch_op:
        batch_op.alter_column(
            "confidence",
            existing_type=sa.Float(),
            type_=sa.String(10),
            existing_nullable=False,
        )

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, confidence FROM decisions")).fetchall()
    for decision_id, confidence in rows:
        connection.execute(
            sa.text("UPDATE decisions SET confidence = :level WHERE id = :id"),
            {"level": _level(confidence), "id": decision_id},
        )
    _rewrite_json_confidence("decisions", "id", "decision_json", _level)
    _rewrite_json_confidence(
        "run_artifacts",
        "id",
        "content_json",
        _level,
        hash_column="content_hash",
        where="content_type = 'research_decision'",
    )
    connection.execute(
        sa.text(
            """
            UPDATE runs
            SET research_schema_version = '2'
            WHERE research_schema_version = '1'
              AND EXISTS (
                  SELECT 1 FROM research_nodes WHERE research_nodes.run_id = runs.id
              )
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    _rewrite_json_confidence("decisions", "id", "decision_json", _score)
    _rewrite_json_confidence(
        "run_artifacts",
        "id",
        "content_json",
        _score,
        hash_column="content_hash",
        where="content_type = 'research_decision'",
    )
    rows = connection.execute(sa.text("SELECT id, confidence FROM decisions")).fetchall()
    for decision_id, confidence in rows:
        connection.execute(
            sa.text("UPDATE decisions SET confidence = :score WHERE id = :id"),
            {"score": _score(confidence), "id": decision_id},
        )
    connection.execute(
        sa.text(
            """
            UPDATE runs
            SET research_schema_version = '1'
            WHERE research_schema_version = '2'
              AND EXISTS (
                  SELECT 1 FROM research_nodes WHERE research_nodes.run_id = runs.id
              )
            """
        )
    )
    with op.batch_alter_table("decisions") as batch_op:
        batch_op.alter_column(
            "confidence",
            existing_type=sa.String(10),
            type_=sa.Float(),
            existing_nullable=False,
        )
