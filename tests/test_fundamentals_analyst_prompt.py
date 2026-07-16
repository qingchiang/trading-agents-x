"""Guard the fundamentals analyst's point-in-time interpretation boundary."""

import inspect

import pytest

import tradingagents.agents.analysts.fundamentals_analyst as fa


@pytest.mark.unit
def test_fundamentals_prompt_preserves_missing_and_historical_data_boundaries():
    source = inspect.getsource(fa)
    assert "missing or unprovided financial fields as unknown, never as zero" in source
    assert "not point-in-time historical data" in source
    assert "must not be presented as evidence" in source
    assert "Do not substitute EBIT, pretax income" in source
