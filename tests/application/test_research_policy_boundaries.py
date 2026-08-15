from datetime import date

from tradingagents.application.research_intervals import DateInterval, DateIntervalSet
from tradingagents.research_sources import JapaneseResearchSource


def test_date_interval_set_normalizes_adjacent_and_overlapping_ranges() -> None:
    intervals = DateIntervalSet(
        (
            DateInterval(date(2026, 7, 5), date(2026, 7, 8)),
            DateInterval(date(2026, 7, 1), date(2026, 7, 3)),
            DateInterval(date(2026, 7, 3), date(2026, 7, 5)),
            DateInterval(date(2026, 7, 9), date(2026, 7, 10)),
        )
    )

    assert intervals.intervals == (
        DateInterval(date(2026, 7, 1), date(2026, 7, 10)),
    )


def test_date_interval_set_reports_only_disjoint_gaps_inside_requested_range() -> None:
    intervals = DateIntervalSet(
        (
            DateInterval(date(2026, 6, 20), date(2026, 7, 2)),
            DateInterval(date(2026, 7, 5), date(2026, 7, 7)),
            DateInterval(date(2026, 7, 9), date(2026, 7, 20)),
        )
    )

    assert intervals.gaps(date(2026, 7, 1), date(2026, 7, 10)) == (
        DateInterval(date(2026, 7, 3), date(2026, 7, 4)),
        DateInterval(date(2026, 7, 8), date(2026, 7, 8)),
    )


def test_japanese_research_source_ids_preserve_persisted_values() -> None:
    assert tuple(source.value for source in JapaneseResearchSource) == (
        "EDINET",
        "TDnet",
        "J-Quants fundamentals",
        "J-Quants adjusted OHLCV",
        "Google News",
        "Social sentiment",
        "Macro observations",
    )
