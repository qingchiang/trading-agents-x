"""TSE trading-day calendar used by the look-ahead-safe publication lags."""
import unittest
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from tradingagents.dataflows.jp.calendar import (
    add_business_days,
    completed_market_date,
    is_tse_open,
    tokyo_today,
)


@pytest.mark.unit
class TseCalendarTests(unittest.TestCase):
    def test_tokyo_today_uses_market_timezone_at_utc_rollover(self):
        utc_now = datetime(2026, 7, 17, 15, 1, tzinfo=UTC)
        self.assertEqual(tokyo_today(utc_now), date(2026, 7, 18))

    def test_weekend_is_closed(self):
        self.assertFalse(is_tse_open(date(2026, 7, 4)))   # Saturday
        self.assertFalse(is_tse_open(date(2026, 7, 5)))   # Sunday
        self.assertTrue(is_tse_open(date(2026, 7, 3)))    # Friday

    def test_national_holiday_is_closed(self):
        # 元日 (New Year's Day) is a Japanese national holiday.
        self.assertFalse(is_tse_open(date(2025, 1, 1)))

    def test_exchange_year_end_days_are_closed(self):
        # Dec 31 and Jan 2-3 are TSE closures but not national holidays.
        self.assertFalse(is_tse_open(date(2025, 12, 31)))
        self.assertFalse(is_tse_open(date(2025, 1, 2)))
        self.assertFalse(is_tse_open(date(2025, 1, 3)))

    def test_add_business_days_skips_weekend(self):
        # Fri + 2 trading days = Tue (skips Sat/Sun).
        self.assertEqual(add_business_days(date(2026, 7, 3), 2), date(2026, 7, 7))

    def test_add_business_days_spans_new_year_break(self):
        # Fri 2024-12-27 + 2 trading days lands on 2025-01-06, ten calendar days
        # later, because of the year-end closure — the case a flat lag mishandles.
        self.assertEqual(add_business_days(date(2024, 12, 27), 2), date(2025, 1, 6))

    def test_add_zero_business_days_is_identity(self):
        self.assertEqual(add_business_days(date(2026, 7, 7), 0), date(2026, 7, 7))

    def test_completed_market_date_waits_until_conservative_daily_bar_cutoff(self):
        tokyo = timezone(timedelta(hours=9))
        before_ready = datetime(2026, 8, 12, 16, 59, tzinfo=tokyo)
        at_ready = datetime(2026, 8, 12, 17, 0, tzinfo=tokyo)

        self.assertEqual(
            completed_market_date(date(2026, 8, 12), before_ready),
            date(2026, 8, 10),
        )
        self.assertEqual(
            completed_market_date(date(2026, 8, 12), at_ready),
            date(2026, 8, 12),
        )

    def test_completed_market_date_keeps_holiday_cutoff_on_prior_session(self):
        holiday = datetime(2026, 8, 11, 12, 0, tzinfo=timezone(timedelta(hours=9)))

        self.assertEqual(
            completed_market_date(date(2026, 8, 11), holiday),
            date(2026, 8, 10),
        )


if __name__ == "__main__":
    unittest.main()
