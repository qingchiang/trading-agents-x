"""China macro parsing, date-boundary, and microscope tests."""

from unittest import mock

import pytest

from tradingagents.dataflows import cn_macro, macro


@pytest.fixture(autouse=True)
def clear_cn_macro_cache():
    cn_macro._series_cache.clear()
    yield
    cn_macro._series_cache.clear()


@pytest.mark.unit
def test_cpi_is_observation_period_bounded_and_non_vintage(monkeypatch):
    monkeypatch.setattr(
        cn_macro,
        "_eastmoney_rows",
        lambda *_args: [
            {"REPORT_DATE": "2026-01-01 00:00:00", "NATIONAL_SAME": 1.2},
            {"REPORT_DATE": "2026-02-01 00:00:00", "NATIONAL_SAME": 1.5},
        ],
    )

    data = cn_macro.fetch_series("cn_cpi", "2026-01-15", 30)

    assert data["points"] == [("2026-01-01", "1.2")]
    assert data["timing"] == "observation-period filtered; non-vintage"


@pytest.mark.unit
def test_lpr_uses_one_year_rate_and_preserves_zero(monkeypatch):
    monkeypatch.setattr(
        cn_macro,
        "_eastmoney_rows",
        lambda *_args: [
            {"TRADE_DATE": "2026-01-10", "LPR1Y": 0},
            {"TRADE_DATE": "2025-01-01", "LPR1Y": 3.1},
        ],
    )

    data = cn_macro.fetch_series("cn_lpr", "2026-01-15", 30)

    assert data["points"] == [("2026-01-10", "0")]


@pytest.mark.unit
def test_unemployment_uses_release_date_and_latest_official_article(monkeypatch):
    listing = """
    <a href="./202602/t20260215_1.html">2月经济运行情况</a>
    <a href="./202601/t20260115_1.html">1月经济运行情况</a>
    """
    article = "2026年1月份，全国城镇调查失业率为 5.1%。"

    def request_text(url, *, label):
        return listing if url.rsplit("/", 2)[-2] + "/" == "zxfb/" else article

    monkeypatch.setattr(cn_macro, "_request_text", request_text)

    data = cn_macro.fetch_series("cn_unemployment", "2026-01-31", 60)

    assert data["points"] == [("2026-01-01", "5.1")]
    assert "release-date filtered" in data["timing"]


@pytest.mark.unit
def test_usd_cny_parses_close_and_drops_future(monkeypatch):
    monkeypatch.setattr(cn_macro, "_fetch_usd_cny_safe", lambda *_args: [])
    monkeypatch.setattr(
        cn_macro,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": {"klines": ["2026-01-10,7.1,7.2", "2026-01-20,7.2,7.3"]}
        },
    )

    data = cn_macro.fetch_series("usd_cny", "2026-01-15", 30)

    assert data["points"] == [("2026-01-10", "7.2")]
    assert data["actual_source"] == "Eastmoney"


@pytest.mark.unit
def test_safe_central_parity_is_primary_and_converts_per_100_usd(monkeypatch):
    monkeypatch.setattr(
        cn_macro,
        "_post_text",
        lambda *_args, **_kwargs: (
            """
        <table><tr><th>日期</th><th>美元</th></tr>
        <tr><td>2026-01-10</td><td>712.34</td></tr>
        <tr><td>2026-01-20</td><td>999.99</td></tr></table>
        """
        ),
    )
    with mock.patch.object(
        cn_macro, "_fetch_usd_cny_eastmoney", side_effect=AssertionError("fallback called")
    ):
        data = cn_macro.fetch_series("usd_cny", "2026-01-15", 30)
    assert data["points"] == [("2026-01-10", "7.1234")]
    assert data["actual_source"] == "SAFE"


@pytest.mark.unit
def test_safe_central_parity_resolves_usd_by_validated_header(monkeypatch):
    monkeypatch.setattr(
        cn_macro,
        "_post_text",
        lambda *_args, **_kwargs: (
            """
        <table><tr><th>日期</th><th>欧元</th><th>美元</th></tr>
        <tr><td>2026-01-10</td><td>800.00</td><td>712.34</td></tr></table>
        """
        ),
    )

    data = cn_macro.fetch_series("usd_cny", "2026-01-15", 30)

    assert data["points"] == [("2026-01-10", "7.1234")]


@pytest.mark.unit
def test_safe_missing_header_uses_eastmoney_fallback(monkeypatch):
    monkeypatch.setattr(
        cn_macro,
        "_post_text",
        lambda *_args, **_kwargs: "<table><tr><td>2026-01-10</td><td>712.34</td></tr></table>",
    )
    monkeypatch.setattr(
        cn_macro,
        "_fetch_usd_cny_eastmoney",
        lambda *_args: [("2026-01-10", "7.2")],
    )

    data = cn_macro.fetch_series("usd_cny", "2026-01-15", 30)

    assert data["points"] == [("2026-01-10", "7.2")]
    assert data["actual_source"] == "Eastmoney"


@pytest.mark.unit
def test_cn_10y_falls_back_to_latest_official_curve_point(monkeypatch):
    monkeypatch.setattr(cn_macro, "_fetch_10y_eastmoney", lambda *_args: [])
    monkeypatch.setattr(
        cn_macro,
        "_request_json",
        lambda *_args, **_kwargs: {
            "records": [
                {"newDateValueCN": "2026-01-14", "yearTermStr": "9.0", "maturityYieldStr": "1.6"},
                {"newDateValueCN": "2026-01-14", "yearTermStr": "10.0", "maturityYieldStr": "1.7"},
                {"newDateValueCN": "2026-01-16", "yearTermStr": "10.0", "maturityYieldStr": "9.9"},
            ]
        },
    )
    data = cn_macro.fetch_series("cn_10y_yield", "2026-01-15", 30)
    assert data["points"] == [("2026-01-14", "1.7")]
    assert data["frequency"] == "Latest official curve snapshot"
    assert "China Foreign Exchange Trade System" in data["timing"]


@pytest.mark.unit
def test_china_alias_dispatch_never_reaches_fred(monkeypatch):
    monkeypatch.setattr(
        cn_macro,
        "fetch_series",
        lambda *_args: {
            "series_id": "cn_pmi",
            "title": "China PMI",
            "units": "index",
            "frequency": "Monthly",
            "seasonal": "",
            "start_date": "2025-01-01",
            "points": [("2026-01-01", "50.1")],
        },
    )
    with mock.patch.object(macro.fred, "get_macro_data", side_effect=AssertionError("FRED called")):
        output = macro.get_macro_indicators("cn_pmi", "2026-01-15")

    assert "## China macro: China PMI" in output
    assert "non-vintage" in output
