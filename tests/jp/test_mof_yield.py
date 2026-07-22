"""MOF JP10Y CSV parsing, publication PIT, and raw-cache contracts."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows.jp import mof_yield
from tradingagents.dataflows.jp.calendar import (
    add_government_business_days,
    is_government_business_day,
)

_TOKYO = ZoneInfo("Asia/Tokyo")


def _csv(*rows: str) -> bytes:
    body = [
        "Interest Rate (July 2026),,,,,,,,,,,(Unit : %)",
        "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y",
        *rows,
        ",,,,,,,,,,,",
        "If the latest CSV cannot be downloaded,,,,,,,,,,",
    ]
    return ("\r\n".join(body) + "\r\n").encode("cp932")


@pytest.fixture(autouse=True)
def clear_cache():
    mof_yield.clear_memory_cache()
    yield
    mof_yield.clear_memory_cache()


@pytest.mark.unit
def test_parser_extracts_10y_and_ignores_missing_and_footer():
    points = mof_yield.parse_csv(
        _csv(
            "2026/7/16,1,2,3,4,5,6,7,8,9,2.719,15",
            "2026/7/17,1,2,3,4,5,6,7,8,9,2.715,15",
            "2026/7/18,1,2,3,4,5,6,7,8,9,-,15",
        )
    )

    assert points == [("2026-07-16", "2.719"), ("2026-07-17", "2.715")]


@pytest.mark.unit
@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", "99"])
def test_parser_rejects_non_finite_or_implausible_yields(value):
    with pytest.raises(mof_yield.MofSchemaError, match="implausible"):
        mof_yield.parse_csv(
            _csv(f"2026/7/17,1,2,3,4,5,6,7,8,9,{value},15")
        )


@pytest.mark.unit
def test_parser_rejects_conflicting_duplicate_dates():
    with pytest.raises(mof_yield.MofSchemaError, match="conflicting duplicate"):
        mof_yield.parse_csv(
            _csv(
                "2026/7/17,1,2,3,4,5,6,7,8,9,2.715,15",
                "2026/7/17,1,2,3,4,5,6,7,8,9,2.800,15",
            )
        )


@pytest.mark.unit
def test_publication_uses_government_calendar_across_holiday_and_year_end():
    assert mof_yield.publication_datetime(date(2026, 7, 17)) == datetime(
        2026, 7, 21, 9, 30, tzinfo=_TOKYO
    )
    assert mof_yield.publication_datetime(date(2026, 12, 30)) == datetime(
        2027, 1, 4, 9, 30, tzinfo=_TOKYO
    )
    assert not is_government_business_day(date(2026, 12, 30))
    assert add_government_business_days(date(2026, 12, 30), 1) == date(2027, 1, 4)


@pytest.mark.unit
def test_fetch_filters_same_observation_before_and_after_0930(monkeypatch):
    history = [("2026-06-30", "2.690")]
    current = [("2026-07-16", "2.719"), ("2026-07-17", "2.715")]
    monkeypatch.setattr(
        mof_yield,
        "_load",
        lambda kind, _now: history if kind == "history" else current,
    )

    before = datetime(2026, 7, 21, 9, 29, tzinfo=_TOKYO)
    after = datetime(2026, 7, 21, 9, 30, tzinfo=_TOKYO)
    before_points = mof_yield.fetch_points(
        date(2026, 6, 1), date(2026, 7, 21), as_of=before, now=before
    )
    after_points = mof_yield.fetch_points(
        date(2026, 6, 1), date(2026, 7, 21), as_of=after, now=after
    )

    assert before_points[-1][0] == "2026-07-16"
    assert after_points[-1][0] == "2026-07-17"


@pytest.mark.unit
def test_historical_analysis_uses_end_of_day_visibility(monkeypatch):
    points = [("2024-02-28", "0.72"), ("2024-02-29", "0.73")]
    monkeypatch.setattr(mof_yield, "_load", lambda *_args: points)
    now = datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO)

    visible = mof_yield.fetch_points(
        date(2024, 2, 1),
        date(2024, 3, 1),
        as_of=mof_yield.analysis_as_of(date(2024, 3, 1), now),
        now=now,
    )

    assert visible == points


@pytest.mark.unit
def test_new_month_uses_prior_current_file_until_history_absorbs_it(monkeypatch):
    history = [("2026-06-30", "2.69")]
    prior_current = [("2026-07-30", "2.7"), ("2026-07-31", "2.71")]
    seen = []

    def load(kind, _now):
        seen.append(kind)
        return history if kind == "history" else prior_current

    monkeypatch.setattr(mof_yield, "_load", load)
    now = datetime(2026, 8, 1, 12, 0, tzinfo=_TOKYO)

    points = mof_yield.fetch_points(
        date(2026, 6, 1), date(2026, 8, 1), as_of=now, now=now
    )

    assert seen == ["history", "current"]
    # July 31 is only published on Monday August 3; July 30 was public on July 31.
    assert points[-1] == ("2026-07-30", "2.7")


@pytest.mark.unit
def test_new_month_skips_empty_current_file_once_history_covers_prior_month(
    monkeypatch,
):
    history = [("2026-07-31", "2.71")]
    load = mock.Mock(return_value=history)
    monkeypatch.setattr(mof_yield, "_load", load)
    now = datetime(2026, 8, 3, 9, 30, tzinfo=_TOKYO)

    points = mof_yield.fetch_points(
        date(2026, 7, 1), date(2026, 8, 3), as_of=now, now=now
    )

    assert points == [("2026-07-31", "2.71")]
    load.assert_called_once_with("history", now)


@pytest.mark.unit
def test_current_cache_cross_instance_hit_and_publication_expiry(monkeypatch):
    body = _csv("2026/7/17,1,2,3,4,5,6,7,8,9,2.715,15")
    download = mock.Mock(return_value=body)
    monkeypatch.setattr(mof_yield, "_download", download)
    now = datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO)

    first = mof_yield._load("current", now)
    path = mof_yield._cache_path("current")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    mof_yield.clear_memory_cache()
    second = mof_yield._load("current", now)

    assert first == second
    assert download.call_count == 1
    assert datetime.fromisoformat(payload["expires_at"]) == datetime(
        2026, 7, 22, 9, 30, tzinfo=_TOKYO
    )


@pytest.mark.unit
def test_history_cache_expires_at_next_month_publication_boundary():
    now = datetime(2026, 7, 31, 10, 0, tzinfo=_TOKYO)
    points = [("2026-06-30", "2.690")]

    assert mof_yield._cache_expiry("history", points, now) == datetime(
        2026, 8, 3, 9, 30, tzinfo=_TOKYO
    )


@pytest.mark.unit
def test_history_cache_waits_only_until_prior_month_final_point_is_published():
    before_publication = datetime(2026, 8, 1, 12, 0, tzinfo=_TOKYO)
    points = [("2026-06-30", "2.69")]

    assert mof_yield._cache_expiry(
        "history", points, before_publication
    ) == datetime(2026, 8, 3, 9, 30, tzinfo=_TOKYO)


@pytest.mark.unit
def test_truncated_prior_month_history_gets_short_retry_cache():
    after_publication = datetime(2026, 8, 3, 10, 0, tzinfo=_TOKYO)
    truncated = [("2026-07-01", "2.7")]

    assert mof_yield._cache_expiry(
        "history", truncated, after_publication
    ) == datetime(2026, 8, 3, 11, 0, tzinfo=_TOKYO)


@pytest.mark.unit
def test_schema_failure_is_not_cached(monkeypatch):
    download = mock.Mock(return_value=b"not,a,valid,csv")
    monkeypatch.setattr(mof_yield, "_download", download)
    now = datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO)

    for _ in range(2):
        with pytest.raises(mof_yield.MofSchemaError, match="header changed"):
            mof_yield._load("current", now)

    assert download.call_count == 2
    assert not os.path.exists(mof_yield._cache_path("current"))
