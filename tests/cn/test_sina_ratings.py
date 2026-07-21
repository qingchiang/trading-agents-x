"""Direct Sina institutional-rating parser and cache tests."""

import pytest

from tradingagents.dataflows.cn import sina_ratings
from tradingagents.dataflows.cn.common import AkShareSchemaError


@pytest.fixture(autouse=True)
def clear_rating_cache():
    sina_ratings._clear_rating_cache()
    yield
    sina_ratings._clear_rating_cache()


def _html(*rows: tuple[str, ...]) -> bytes:
    headings = (
        "股票代码",
        "股票名称",
        "目标价",
        "最新评级",
        "评级机构",
        "分析师",
        "评级日期↓",
    )
    header = "".join(f"<th>{heading}</th>" for heading in headings)
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        f'<meta charset="utf-8"><table><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    ).encode()


@pytest.mark.unit
def test_parse_sina_rows_normalizes_targets_and_rating_changes():
    content = _html(
        ("600519", "贵州茅台", "100-120", "增持", "Broker", "A", "2026-01-01"),
        ("600519", "贵州茅台", "130", "买入", "Broker", "B", "2026-01-10"),
    )

    rows = sina_ratings._parse_rows(content, "600519")
    latest = next(row for row in rows if row["published"].isoformat() == "2026-01-10")

    assert latest["target_low"] == 130
    assert latest["target_high"] == 130
    assert latest["rating_change"] == "增持 -> 买入"


@pytest.mark.unit
def test_parse_sina_rows_rejects_another_stock_code():
    content = _html(
        ("000001", "平安银行", "10", "买入", "Broker", "A", "2026-01-10"),
    )

    with pytest.raises(AkShareSchemaError, match="another stock code"):
        sina_ratings._parse_rows(content, "600519")


@pytest.mark.unit
def test_rating_rows_cache_same_cutoff_and_filter_locally(monkeypatch):
    calls = []
    content = _html(
        ("600519", "贵州茅台", "100", "买入", "Broker", "A", "2025-12-20"),
        ("600519", "贵州茅台", "120", "买入", "Broker", "A", "2026-01-05"),
    )

    def request(code):
        calls.append(code)
        return content

    monkeypatch.setattr(sina_ratings, "_request_page", request)

    recent = sina_ratings.rating_rows("600519.SS", "2026-01-01", "2026-01-10")
    wider = sina_ratings.rating_rows("600519.SS", "2025-12-01", "2026-01-10")

    assert [row["published"].isoformat() for row in recent] == ["2026-01-05"]
    assert len(wider) == 2
    assert calls == ["600519"]


@pytest.mark.unit
def test_rating_cache_expires(monkeypatch):
    clock = [0.0]
    calls = []
    content = _html(
        ("600519", "贵州茅台", "120", "买入", "Broker", "A", "2026-01-05"),
    )
    monkeypatch.setattr(sina_ratings.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        sina_ratings,
        "_request_page",
        lambda code: calls.append(code) or content,
    )

    sina_ratings.rating_rows("600519.SS", "2026-01-01", "2026-01-10")
    sina_ratings.rating_rows("600519.SS", "2026-01-01", "2026-01-10")
    clock[0] = sina_ratings._CACHE_TTL_SECONDS + 1
    sina_ratings.rating_rows("600519.SS", "2026-01-01", "2026-01-10")

    assert calls == ["600519", "600519"]
