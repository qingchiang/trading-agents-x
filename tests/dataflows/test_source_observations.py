from datetime import UTC, date, datetime

import pandas as pd

from tradingagents.dataflows import y_finance


def test_us_statement_exposes_period_values_without_claiming_filing_date(monkeypatch):
    from tradingagents.dataflows.source_observations import capture_observations

    class Stock:
        quarterly_cashflow = pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [150.0, -100.0]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    monkeypatch.setattr(y_finance.yf, "Ticker", lambda _: Stock())
    with capture_observations() as observations:
        output = y_finance.get_cashflow("GOOG", "quarterly", "2026-09-05")
    assert "150" in output
    assert len(observations) == 1
    observation = observations[0]
    assert observation.effective_date == date(2026, 6, 30)
    assert observation.available_at is None
    assert observation.available_on is None
    assert observation.values["Capital Expenditure"] == -100.0
    assert observation.retrieved_at.tzinfo is not None


def test_china_statement_observations_retain_visibility_and_cumulative_basis(monkeypatch):
    from tests.cn.test_cn_statements import _frame
    from tradingagents.dataflows.cn import cn_statements
    from tradingagents.dataflows.source_observations import capture_observations

    monkeypatch.setattr(cn_statements, "fetch_finance_records", lambda *_: ("600309.SS", _frame()))
    monkeypatch.setattr(cn_statements, "get_company_profile", lambda _: pd.DataFrame())
    monkeypatch.setattr(cn_statements, "get_statement_frame", lambda *_: None)
    with capture_observations() as observations:
        cn_statements.get_income_statement("600309.SS", curr_date="2026-03-21")
    assert observations[0].available_on == date(2026, 3, 21)
    assert observations[0].values["period_basis"] == "YTD"
    assert observations[0].values["Revenue"] == 1000


def test_japan_margin_publishes_conservative_release_date(monkeypatch):
    from tradingagents.dataflows.jp import jquants_sentiment
    from tradingagents.dataflows.source_observations import capture_observations

    monkeypatch.setattr(
        jquants_sentiment,
        "fetch_records",
        lambda *a: [
            {"Date": "2026-08-28", "LongVol": 90, "ShrtVol": 10},
        ],
    )
    with capture_observations() as observations:
        jquants_sentiment.get_margin_balance("9984.T", "2026-09-05")
    assert observations[0].effective_date == date(2026, 8, 28)
    assert observations[0].available_on == date(2026, 9, 1)
    assert "T+2" in observations[0].timing


def test_incremental_admits_statement_rows_from_the_shared_producer(monkeypatch):
    from tests.dataflows.test_incremental_us_collector import _request
    from tradingagents.application.incremental_collection import normalize_incremental_collection
    from tradingagents.dataflows.incremental_us import collect_us_incremental
    from tradingagents.provenance import ProvenanceRecord, attach_provenance

    class Stock:
        quarterly_cashflow = pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [150.0, -100.0]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    monkeypatch.setattr(y_finance.yf, "Ticker", lambda _: Stock())

    def route(method, *args, **kwargs):
        if method == "get_cashflow":
            return y_finance.get_cashflow(*args)
        return attach_provenance(
            "live overview",
            ProvenanceRecord(
                evidence=method,
                source="yfinance",
                requested="2026-09-05",
                effective="live snapshot",
                timing="live non-point-in-time",
            ),
        )

    now = datetime.now(UTC)
    request = _request(
        enabled_domains=("fundamentals",),
        baseline=date(2026, 8, 29),
        target=now.date(),
        window_start=datetime(2026, 8, 29, tzinfo=UTC),
        window_end=now,
    )
    result = collect_us_incremental(request, route_to_vendor=route)
    _, items, _ = normalize_incremental_collection(request, result, sealed_at=datetime.now(UTC))
    statement = next(item for item in items if item.evidence_type == "financial_cashflow")
    assert '"Capital Expenditure": -100.0' in statement.content
    assert statement.origins[0].temporal_scope.value == "live_only"


def test_professional_signal_enters_incremental_and_full_with_same_identity(monkeypatch):
    from tests.dataflows.test_incremental_jp_collector import _request
    from tradingagents.application.incremental_collection import normalize_incremental_collection
    from tradingagents.dataflows import incremental_jp
    from tradingagents.dataflows.market_signals import (
        FetchedSentimentSignal,
        sentiment_signal_specs,
    )
    from tradingagents.dataflows.source_observations import SourceObservation
    from tradingagents.graph.research_graph import _collect_evidence

    observed = SourceObservation(
        "J-Quants",
        "margin_balances",
        "2026-07-17",
        {"LongVol": 90, "ShrtVol": 10},
        datetime(2026, 7, 24, 6, tzinfo=UTC),
        effective_date=date(2026, 7, 17),
        available_on=date(2026, 7, 21),
        timing="inferred T+2 publication",
    )
    fetched = FetchedSentimentSignal(
        sentiment_signal_specs("7203.T")[1], "margin", observations=(observed,)
    )
    monkeypatch.setattr(incremental_jp, "fetch_sentiment_signals", lambda *a: (fetched,))
    request = _request(enabled_domains=("social",))
    result = incremental_jp.collect_japan_incremental(request)
    _, admitted, _ = normalize_incremental_collection(
        request,
        result,
        sealed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    full = _collect_evidence(
        [],
        "",
        requested_date=request.analysis_cutoff,
        analyst="social",
        prefetched_blocks=[{"source_observation": observed.dump()}],
    )
    assert len(admitted) == 1
    assert (
        admitted[0].provenance["observation_identity"] == full[0].provenance["observation_identity"]
    )
