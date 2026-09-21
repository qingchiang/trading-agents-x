"""Explicit data requests retain independent routing policy."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tradingagents.configuration.defaults import build_default_config
from tradingagents.data.context import DataRequestContext
from tradingagents.data.interface import get_vendor


def test_context_copies_caller_policy():
    config = build_default_config()
    context = DataRequestContext(config)
    config["data_vendors"]["core_stock_apis"] = "alpha_vantage"
    config["tool_vendors"]["get_stock_data"] = "alpha_vantage"
    assert context.config["data_vendors"]["core_stock_apis"] == "yfinance"
    assert "get_stock_data" not in context.config["tool_vendors"]
    with pytest.raises(TypeError):
        context.config["data_vendors"] = {}


def test_concurrent_explicit_routing_policies_are_independent():
    barrier = Barrier(2)

    def route(vendor):
        config = build_default_config()
        config["tool_vendors"]["get_stock_data"] = vendor
        context = DataRequestContext(config)
        barrier.wait(timeout=5)
        return get_vendor("core_stock_apis", "get_stock_data", "", context.config)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(route, "yfinance")
        second = executor.submit(route, "alpha_vantage")
    assert first.result() == "yfinance"
    assert second.result() == "alpha_vantage"
