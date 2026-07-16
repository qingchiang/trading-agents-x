"""Shared analysis-date boundary helpers."""

import pytest

from tradingagents.dataflows.lookahead import lookback_start_date


@pytest.mark.unit
def test_lookback_start_date_matches_inclusive_vendor_window_convention():
    assert lookback_start_date("2026-01-15", 14) == "2026-01-01"
    assert lookback_start_date("2026-01-15", 7) == "2026-01-08"


@pytest.mark.unit
@pytest.mark.parametrize("value", [-1, True, 1.5, "7"])
def test_lookback_start_date_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="lookback_days"):
        lookback_start_date("2026-01-15", value)
