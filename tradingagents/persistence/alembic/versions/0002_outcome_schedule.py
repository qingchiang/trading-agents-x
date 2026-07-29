"""Persist due times for low-priority outcome settlement.

Revision ID: 0002_outcome_schedule
Revises: 0001_application_core
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision: str = "0002_outcome_schedule"
down_revision: str | Sequence[str] | None = "0001_application_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MARKET_TIMEZONES = {
    ".T": "Asia/Tokyo",
    ".SS": "Asia/Shanghai",
    ".SZ": "Asia/Shanghai",
}


def _timezone_for(ticker: str, asset_type: str):
    if asset_type == "crypto":
        return timezone.utc
    upper = ticker.upper()
    for suffix, name in _MARKET_TIMEZONES.items():
        if upper.endswith(suffix):
            return ZoneInfo(name)
    return ZoneInfo("America/New_York")


def _earliest_check(
    ticker: str,
    asset_type: str,
    analysis_date: date,
    holding_intervals: int,
) -> datetime:
    required_closes = holding_intervals + 1
    candidate = analysis_date
    observed = 0
    while observed < required_closes:
        if asset_type == "crypto" or candidate.weekday() < 5:
            observed += 1
        candidate += timedelta(days=1)
    local_midnight = datetime.combine(
        candidate,
        time.min,
        tzinfo=_timezone_for(ticker, asset_type),
    )
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    op.add_column(
        "outcomes",
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_outcomes_due",
        "outcomes",
        ["status", "next_check_at"],
        unique=False,
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT outcomes.id, outcomes.holding_intervals,
                   outcomes.last_checked_at, decisions.ticker,
                   decisions.asset_type, decisions.analysis_date
            FROM outcomes
            JOIN decisions ON decisions.id = outcomes.decision_id
            WHERE outcomes.status = 'pending'
            """
        )
    ).mappings()
    for row in rows:
        analysis_date = row["analysis_date"]
        if isinstance(analysis_date, str):
            analysis_date = date.fromisoformat(analysis_date)
        due = _earliest_check(
            row["ticker"],
            row["asset_type"],
            analysis_date,
            row["holding_intervals"],
        )
        last_checked = row["last_checked_at"]
        if isinstance(last_checked, str):
            last_checked = datetime.fromisoformat(last_checked)
        if last_checked is not None:
            due = max(due, last_checked + timedelta(days=1))
        connection.execute(
            sa.text(
                "UPDATE outcomes SET next_check_at = :due WHERE id = :id"
            ),
            {"due": due, "id": row["id"]},
        )


def downgrade() -> None:
    op.drop_index("ix_outcomes_due", table_name="outcomes")
    op.drop_column("outcomes", "next_check_at")
