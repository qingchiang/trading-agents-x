"""Separate outcome observation, reflection, and feedback lifecycles.

Revision ID: 0009_outcome_feedback_lifecycle
Revises: 0008_evidence_closed_revisions
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_outcome_feedback_lifecycle"
down_revision: str | Sequence[str] | None = "0008_evidence_closed_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METHOD_CATEGORY = "short_term_relative_return"
METHOD_VERSION = "short_term_relative_return.v1"
PRICE_SEMANTICS = "exchange_local_daily_close"
ADJUSTMENT_SEMANTICS = "split_and_dividend_adjusted"
HORIZON_LIMIT = (
    "Five completed aligned intervals provide short-term relative-return "
    "feedback only and do not prove or disprove a medium- or long-horizon thesis."
)


def upgrade() -> None:
    horizon_sql = HORIZON_LIMIT.replace("'", "''")
    # SQLite batch recreation of the parent outcomes table fires the legacy
    # reflections ON DELETE CASCADE. Preserve those rows explicitly.
    op.execute(
        "CREATE TABLE _reflections_0009_backup AS "
        "SELECT id, outcome_id, text, created_at FROM reflections"
    )
    with op.batch_alter_table("outcomes") as batch:
        batch.add_column(sa.Column("research_revision_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column("market_timezone", sa.String(80), nullable=False, server_default="UTC")
        )
        batch.add_column(
            sa.Column(
                "method_category",
                sa.String(80),
                nullable=False,
                server_default=METHOD_CATEGORY,
            )
        )
        batch.add_column(
            sa.Column(
                "method_version",
                sa.String(80),
                nullable=False,
                server_default=METHOD_VERSION,
            )
        )
        batch.add_column(
            sa.Column(
                "price_semantics",
                sa.String(80),
                nullable=False,
                server_default=PRICE_SEMANTICS,
            )
        )
        batch.add_column(
            sa.Column(
                "adjustment_semantics",
                sa.String(80),
                nullable=False,
                server_default=ADJUSTMENT_SEMANTICS,
            )
        )
        batch.add_column(
            sa.Column(
                "horizon_limit",
                sa.Text(),
                nullable=False,
                server_default=HORIZON_LIMIT,
            )
        )
        batch.add_column(
            sa.Column("limitations_json", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("data_available_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_outcomes_research_revision_id",
            "research_revisions",
            ["research_revision_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_outcomes_research_revision_id",
            ["research_revision_id"],
            unique=False,
        )
    op.execute(
        "INSERT OR IGNORE INTO reflections (id, outcome_id, text, created_at) "
        "SELECT id, outcome_id, text, created_at FROM _reflections_0009_backup"
    )
    op.drop_table("_reflections_0009_backup")

    op.execute(
        "UPDATE outcomes SET data_available_at = resolved_at "
        "WHERE status = 'resolved' AND data_available_at IS NULL"
    )
    op.execute(
        "UPDATE outcomes SET market_timezone = CASE "
        "WHEN (SELECT ticker FROM decisions WHERE decisions.id = outcomes.decision_id) "
        "LIKE '%.T' THEN 'Asia/Tokyo' "
        "WHEN (SELECT ticker FROM decisions WHERE decisions.id = outcomes.decision_id) "
        "LIKE '%.SS' OR (SELECT ticker FROM decisions WHERE decisions.id = "
        "outcomes.decision_id) LIKE '%.SZ' THEN 'Asia/Shanghai' "
        "ELSE 'America/New_York' END"
    )
    op.execute(
        "UPDATE outcomes SET research_revision_id = ("
        "SELECT research_revisions.id FROM research_revisions "
        "JOIN decisions ON decisions.run_id = research_revisions.producing_run_id "
        "WHERE decisions.id = outcomes.decision_id)"
    )

    with op.batch_alter_table("reflections") as batch:
        batch.add_column(
            sa.Column("status", sa.String(30), nullable=False, server_default="generated")
        )
        batch.add_column(sa.Column("candidate_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("generated_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_attempted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("next_retry_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(80), nullable=True))
        batch.alter_column("text", existing_type=sa.Text(), nullable=True)
        batch.create_index("ix_reflections_status", ["status"], unique=False)
    op.execute(
        "UPDATE reflections SET generated_at = created_at, last_attempted_at = created_at "
        "WHERE status = 'generated'"
    )

    op.create_table(
        "outcome_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reflection_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("method_category", sa.String(80), nullable=False),
        sa.Column("horizon_limit", sa.Text(), nullable=False),
        sa.Column("applicability_json", sa.JSON(), nullable=False),
        sa.Column("qualified_at", sa.DateTime(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["reflection_id"], ["reflections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reflection_id"),
    )
    op.create_index(
        "ix_outcome_feedback_reflection_id",
        "outcome_feedback",
        ["reflection_id"],
        unique=True,
    )
    op.create_index(
        "ix_outcome_feedback_status", "outcome_feedback", ["status"], unique=False
    )
    op.create_index(
        "ix_outcome_feedback_available_at",
        "outcome_feedback",
        ["available_at"],
        unique=False,
    )
    op.execute(
        "UPDATE reflections SET candidate_json = json_object("
        "'schema_version', '1', 'lesson', text, 'method_category', "
        f"'{METHOD_CATEGORY}', 'horizon_limit', '{horizon_sql}') "
        "WHERE status = 'generated'"
    )
    op.execute(
        "INSERT INTO outcome_feedback (reflection_id, status, reasons_json, "
        "method_category, horizon_limit, applicability_json, qualified_at, "
        "available_at, retired_at) "
        "SELECT reflections.id, 'ineligible', '[\"legacy_unqualified_reflection\"]', "
        f"'{METHOD_CATEGORY}', '{horizon_sql}', "
        "json_object('schema_version', '1', 'instrument', decisions.ticker, "
        "'market', decisions.market, 'research_stages', json_array('analysis_methodology'), "
        "'research_domains', json_array('cross_domain'), 'method_category', "
        f"'{METHOD_CATEGORY}', 'horizon', 'short_term'), "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL FROM reflections "
        "JOIN outcomes ON outcomes.id = reflections.outcome_id "
        "JOIN decisions ON decisions.id = outcomes.decision_id "
        "WHERE reflections.status = 'generated'"
    )


def downgrade() -> None:
    op.drop_index("ix_outcome_feedback_available_at", table_name="outcome_feedback")
    op.drop_index("ix_outcome_feedback_status", table_name="outcome_feedback")
    op.drop_index("ix_outcome_feedback_reflection_id", table_name="outcome_feedback")
    op.drop_table("outcome_feedback")
    op.execute("DELETE FROM reflections WHERE text IS NULL")
    with op.batch_alter_table("reflections") as batch:
        batch.drop_index("ix_reflections_status")
        batch.alter_column("text", existing_type=sa.Text(), nullable=False)
        batch.drop_column("error_code")
        batch.drop_column("next_retry_at")
        batch.drop_column("last_attempted_at")
        batch.drop_column("generated_at")
        batch.drop_column("candidate_json")
        batch.drop_column("status")
    op.execute(
        "CREATE TABLE _reflections_0009_backup AS "
        "SELECT id, outcome_id, text, created_at FROM reflections"
    )
    with op.batch_alter_table("outcomes") as batch:
        batch.drop_index("ix_outcomes_research_revision_id")
        batch.drop_constraint("fk_outcomes_research_revision_id", type_="foreignkey")
        batch.drop_column("data_available_at")
        batch.drop_column("limitations_json")
        batch.drop_column("horizon_limit")
        batch.drop_column("adjustment_semantics")
        batch.drop_column("price_semantics")
        batch.drop_column("method_version")
        batch.drop_column("method_category")
        batch.drop_column("market_timezone")
        batch.drop_column("research_revision_id")
    op.execute(
        "INSERT OR IGNORE INTO reflections (id, outcome_id, text, created_at) "
        "SELECT id, outcome_id, text, created_at FROM _reflections_0009_backup"
    )
    op.drop_table("_reflections_0009_backup")
