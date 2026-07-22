import copy
from unittest import mock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.dataflows.config import set_config
from tradingagents.provenance import ProvenanceRecord, attach_provenance


@pytest.fixture(autouse=True)
def _reset_config():
    previous = config_module._config
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = previous


def _final_llm():
    llm = mock.MagicMock()
    llm.bind_tools.return_value = RunnableLambda(
        lambda _prompt: AIMessage(content="MODEL REPORT")
    )
    return llm


def _state(tool_content: str):
    return {
        "company_of_interest": "6501.T",
        "trade_date": "2026-07-17",
        "asset_type": "stock",
        "messages": [ToolMessage(content=tool_content, tool_call_id="call-1")],
    }


@pytest.mark.unit
def test_market_final_report_keeps_snapshot_source_and_effective_session():
    set_config({"provenance_appendix": True})
    content = attach_provenance(
        "SNAPSHOT",
        ProvenanceRecord(
            evidence="get_verified_market_snapshot",
            source="J-Quants",
            requested="2026-07-17",
            effective="2026-07-16",
            timing="market-date filtered",
        ),
    )
    result = create_market_analyst(_final_llm())(_state(content))

    report = result["market_report"]
    assert report == result["messages"][0].content
    assert "| get_verified_market_snapshot | J-Quants | 2026-07-17 | 2026-07-16 |" in report
    assert report.count("## Data Provenance") == 1


@pytest.mark.unit
def test_fundamentals_final_report_distinguishes_sources_and_missing_tools():
    set_config({"provenance_appendix": True})
    content = attach_provenance(
        "STATEMENT",
        ProvenanceRecord(
            evidence="get_income_statement",
            source="J-Quants official summary",
            requested="2026-07-17",
            effective="disclosures <= 2026-07-17",
            timing="disclosure-date filtered",
        ),
        ProvenanceRecord(
            evidence="get_income_statement",
            source="yfinance curated detail",
            requested="2026-07-17",
            effective="current statement frame",
            timing="live non-point-in-time",
            retrieved_at="2026-07-17T01:02:03+00:00",
        ),
    )
    result = create_fundamentals_analyst(_final_llm())(_state(content))

    report = result["fundamentals_report"]
    assert report == result["messages"][0].content
    assert "J-Quants official summary" in report
    assert "yfinance curated detail" in report
    assert "live non-point-in-time; retrieved 2026-07-17T01:02:03+00:00" in report
    assert "| balance sheet | — | 2026-07-17 | — | not requested |" in report
    assert "| cash flow statement | — | 2026-07-17 | — | not requested |" in report


@pytest.mark.unit
def test_market_report_omits_appendix_by_default():
    content = attach_provenance(
        "SNAPSHOT",
        ProvenanceRecord(
            evidence="get_verified_market_snapshot",
            source="J-Quants",
            effective="2026-07-17",
            timing="market-date filtered",
        ),
    )
    result = create_market_analyst(_final_llm())(_state(content))

    assert result["market_report"] == "MODEL REPORT"
    assert result["messages"][0].content == "MODEL REPORT"
