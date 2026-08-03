"""Market-local boundaries for retrieval-time evidence."""

from datetime import date, datetime, timedelta, timezone

import pytest

from tradingagents.dataflows.lookahead import is_near_live


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ticker", "now", "local_today"),
    [
        (
            "6501.T",
            datetime(2026, 7, 28, 15, 30, tzinfo=timezone.utc),
            date(2026, 7, 29),
        ),
        (
            "600519.SS",
            datetime(2026, 7, 28, 16, 30, tzinfo=timezone.utc),
            date(2026, 7, 29),
        ),
        (
            "NVDA",
            datetime(2026, 7, 29, 1, tzinfo=timezone.utc),
            date(2026, 7, 28),
        ),
        (
            "BTC-USD",
            datetime(2026, 7, 28, 23, 30, tzinfo=timezone.utc),
            date(2026, 7, 28),
        ),
    ],
)
def test_near_live_uses_the_instrument_market_date(
    ticker: str,
    now: datetime,
    local_today: date,
) -> None:
    assert is_near_live(local_today.isoformat(), ticker, now=now)
    assert is_near_live(
        (local_today - timedelta(days=5)).isoformat(),
        ticker,
        now=now,
    )
    assert not is_near_live(
        (local_today - timedelta(days=6)).isoformat(),
        ticker,
        now=now,
    )
    assert not is_near_live(
        (local_today + timedelta(days=1)).isoformat(),
        ticker,
        now=now,
    )


@pytest.mark.unit
def test_near_live_rejects_malformed_cutoffs() -> None:
    assert not is_near_live("not-a-date", "6501.T")
