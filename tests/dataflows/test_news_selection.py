from datetime import date


def test_selection_reserves_older_periods_when_recent_feed_is_busy():
    from tradingagents.dataflows.news_selection import select_temporal

    rows = [(f"recent-{i}", date(2026, 9, 4)) for i in range(40)]
    rows += [
        ("early", date(2026, 8, 8)),
        ("middle", date(2026, 8, 17)),
        ("late", date(2026, 8, 26)),
    ]
    result = select_temporal(rows, 6, "2026-08-07", "2026-09-05", published=lambda row: row[1])
    assert len(result) == 6
    assert "early" in {row[0] for row in result}
    assert "middle" in {row[0] for row in result}
    assert sum(row[0].startswith("recent") for row in result) == 4


def test_china_borrows_unused_source_quota_without_another_fetch():
    from tradingagents.dataflows.news_selection import merge_news_blocks

    blocks = ["## official\n\n### filing\nDisclosed: 2026-09-01"]
    blocks.append(
        "## research\n\n" + "\n\n".join(f"### rating {i}\nPublished: 2026-09-02" for i in range(30))
    )
    kept, counts = merge_news_blocks(blocks, 30, "2026-08-07", "2026-09-05", quotas=[15, 7])
    assert [count.kept for count in counts] == [1, 29]
    assert sum(block.count("### ") for block in kept) == 30


def test_company_selection_keeps_latest_intraday_items_from_cache_order():
    from tradingagents.dataflows.news_selection import merge_news_blocks, split_candidates

    block = "## news\n\n" + "\n\n".join(
        f"### event {hour}\nPublished: 2026-09-05T{hour}:00:00Z"
        for hour in ("08", "09", "10")
    )
    selected, counts = merge_news_blocks([block], 2, "2026-09-01", "2026-09-05")
    assert [row.title for row in split_candidates(selected[0])[1]] == ["event 10", "event 09"]
    assert counts[0].cap_omitted == 1
