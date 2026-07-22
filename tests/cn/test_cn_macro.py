"""China macro parsing, date-boundary, and microscope tests."""

from unittest import mock

import pytest

from tradingagents.dataflows import cn_macro, macro


@pytest.fixture(autouse=True)
def clear_cn_macro_cache():
    cn_macro._series_cache.clear()
    cn_macro._nbs_index_cache.clear()
    yield
    cn_macro._series_cache.clear()
    cn_macro._nbs_index_cache.clear()


@pytest.mark.unit
def test_cpi_eastmoney_fallback_is_observation_period_bounded_and_non_vintage(
    monkeypatch,
):
    monkeypatch.setattr(cn_macro, "_fetch_nbs_indicator", lambda *_args: None)
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
    assert data["actual_source"] == "Eastmoney"
    assert data["fallback_reason"] == "NBS returned no usable recent official release"
    assert "observation-period filtered; non-vintage" in data["timing"]


@pytest.mark.unit
def test_cpi_prefers_latest_eligible_nbs_release_and_records_dates(monkeypatch):
    listing = """
    <a href="./202608/t20260810_2.html">2026年7月份居民消费价格同比上涨1.4%</a>
    <a href="./202607/t20260709_1.html">2026年6月份居民消费价格同比上涨1.0%</a>
    """
    article = "<p>2026年6月份，全国居民消费价格同比上涨1.0%。</p>"
    requested = []

    def request_text(url, *, label):
        requested.append(url)
        return listing if url in cn_macro._NBS_INDEX_PAGES else article

    monkeypatch.setattr(cn_macro, "_request_text", request_text)
    monkeypatch.setattr(
        cn_macro,
        "_fetch_economy",
        lambda *_args: (_ for _ in ()).throw(AssertionError("fallback called")),
    )

    data = cn_macro.fetch_series("cn_cpi", "2026-07-21", 60)

    assert data["points"] == [("2026-06-01", "1")]
    assert data["actual_source"] == cn_macro._NBS_SOURCE
    assert data["release_date"] == "2026-07-09"
    assert data["observation_period"] == "2026-06"
    assert "fallback_reason" not in data
    assert "release date=2026-07-09" in data["timing"]
    assert not any("20260810_2" in url for url in requested)


@pytest.mark.unit
def test_gdp_nbs_primary_preserves_cumulative_yoy_not_single_quarter(monkeypatch):
    listing = """
    <a href="./202607/t20260716_1.html">
      2026年二季度和上半年国内生产总值初步核算结果
    </a>
    """
    article = """
    <table>
      <tr><th></th><th colspan="2">绝对额（亿元）</th>
          <th colspan="2">比上年同期增长（%）</th></tr>
      <tr><th></th><th>二季度</th><th>上半年</th><th>二季度</th><th>上半年</th></tr>
      <tr><td>GDP</td><td>361511</td><td>695704</td><td>4.3</td><td>4.7</td></tr>
    </table>
    """
    monkeypatch.setattr(
        cn_macro,
        "_request_text",
        lambda url, *, label: listing if url in cn_macro._NBS_INDEX_PAGES else article,
    )

    data = cn_macro.fetch_series("cn_gdp", "2026-07-21", 180)

    assert data["points"] == [("2026-06-30", "4.7")]
    assert data["release_date"] == "2026-07-16"
    assert data["observation_period"] == "2026 H1"
    assert data["growth_basis"] == "cumulative year-to-date YoY"
    assert "growth basis=cumulative year-to-date YoY" in data["timing"]


@pytest.mark.unit
def test_pmi_uses_second_bounded_release_index_page(monkeypatch):
    main_listing = """
    <a href="./202607/t20260716_1.html">
      2026年二季度和上半年国内生产总值初步核算结果
    </a>
    """
    second_listing = """
    <a href="./202606/t20260630_1.html">2026年6月中国采购经理指数运行情况</a>
    """
    article = "<p>6 月份，制造业采购经理指数（ PMI ）为 50.3%，比上月上升。</p>"
    requested = []

    def request_text(url, *, label):
        requested.append(url)
        if url == cn_macro._NBS_INDEX_PAGES[0]:
            return main_listing
        if url == cn_macro._NBS_INDEX_PAGES[1]:
            return second_listing
        return article

    monkeypatch.setattr(cn_macro, "_request_text", request_text)

    data = cn_macro.fetch_series("cn_pmi", "2026-07-21", 60)

    assert data["points"] == [("2026-06-01", "50.3")]
    assert data["release_date"] == "2026-06-30"
    assert data["observation_period"] == "2026-06"
    assert requested[:2] == list(cn_macro._NBS_INDEX_PAGES)


@pytest.mark.unit
def test_nbs_schema_failure_falls_back_without_exposing_exception(monkeypatch):
    listing = """
    <a href="./202607/t20260709_1.html">2026年6月份居民消费价格同比上涨1.0%</a>
    """
    monkeypatch.setattr(
        cn_macro,
        "_request_text",
        lambda url, *, label: listing if url in cn_macro._NBS_INDEX_PAGES else "changed",
    )
    monkeypatch.setattr(
        cn_macro,
        "_eastmoney_rows",
        lambda *_args: [{"REPORT_DATE": "2026-06-01", "NATIONAL_SAME": 0.8}],
    )

    data = cn_macro.fetch_series("cn_cpi", "2026-07-21", 60)

    assert data["points"] == [("2026-06-01", "0.8")]
    assert data["actual_source"] == "Eastmoney"
    assert data["fallback_reason"] == "NBS primary response schema changed"


@pytest.mark.unit
def test_nbs_failure_and_empty_eastmoney_fallback_is_not_a_successful_empty_window(
    monkeypatch,
):
    attempts = 0

    def failed_primary(*_args):
        nonlocal attempts
        attempts += 1
        raise cn_macro.AkShareRequestError("blocked upstream detail")

    monkeypatch.setattr(cn_macro, "_fetch_nbs_indicator", failed_primary)
    monkeypatch.setattr(cn_macro, "_fetch_economy", lambda *_args: [])

    for _ in range(2):
        with pytest.raises(
            cn_macro.AkShareRequestError,
            match=(
                "NBS primary retrieval unavailable; "
                "Eastmoney returned no usable observations"
            ),
        ):
            cn_macro.fetch_series("cn_cpi", "2026-07-21", 60)

    assert attempts == 2


@pytest.mark.unit
def test_no_recent_nbs_release_and_empty_eastmoney_remains_a_normal_empty_window(
    monkeypatch,
):
    monkeypatch.setattr(cn_macro, "_fetch_nbs_indicator", lambda *_args: None)
    monkeypatch.setattr(cn_macro, "_fetch_economy", lambda *_args: [])

    assert cn_macro.fetch_series("cn_cpi", "2026-07-21", 1) is None


@pytest.mark.unit
def test_nbs_release_after_analysis_date_is_not_injected(monkeypatch):
    listing = """
    <a href="./202607/t20260709_1.html">2026年6月份居民消费价格同比上涨1.0%</a>
    """
    article_requested = False

    def request_text(url, *, label):
        nonlocal article_requested
        if url not in cn_macro._NBS_INDEX_PAGES:
            article_requested = True
        return listing

    monkeypatch.setattr(cn_macro, "_request_text", request_text)
    monkeypatch.setattr(
        cn_macro,
        "_eastmoney_rows",
        lambda *_args: [{"REPORT_DATE": "2026-05-01", "NATIONAL_SAME": 0.7}],
    )

    data = cn_macro.fetch_series("cn_cpi", "2026-06-30", 60)

    assert data["points"] == [("2026-05-01", "0.7")]
    assert data["actual_source"] == "Eastmoney"
    assert data["fallback_reason"] == "NBS returned no usable recent official release"
    assert article_requested is False


@pytest.mark.unit
def test_nbs_listing_is_shared_across_indicators_for_same_analysis_date(monkeypatch):
    listing = """
    <a href="./202607/t20260716_1.html">
      2026年二季度和上半年国内生产总值初步核算结果
    </a>
    <a href="./202607/t20260709_1.html">2026年6月份居民消费价格同比上涨1.0%</a>
    """
    articles = {
        "t20260709_1": "<p>2026年6月份，全国居民消费价格同比上涨1.0%。</p>",
        "t20260716_1": (
            "<table><tr><td>GDP</td><td>361511</td><td>695704</td>"
            "<td>4.3</td><td>4.7</td></tr></table>"
        ),
    }
    listing_requests = 0

    def request_text(url, *, label):
        nonlocal listing_requests
        if url == cn_macro._NBS_INDEX_PAGES[0]:
            listing_requests += 1
            return listing
        return next(value for key, value in articles.items() if key in url)

    monkeypatch.setattr(cn_macro, "_request_text", request_text)

    assert cn_macro.fetch_series("cn_cpi", "2026-07-21", 180)
    assert cn_macro.fetch_series("cn_gdp", "2026-07-21", 180)
    assert listing_requests == 1


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
    assert data["fallback_reason"] == "SAFE returned no usable observations"


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
    assert "fallback_reason" not in data


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
    assert data["fallback_reason"] == "SAFE primary retrieval unavailable"


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
    assert data["fallback_reason"] == "Eastmoney returned no usable observations"


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
