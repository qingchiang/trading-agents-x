from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradingagents.application.evidence_workset import (
    build_market_data_artifact,
    market_analytical_views,
    parse_ohlcv_frame,
)
from tradingagents.provenance import ProvenanceRecord, attach_provenance


def _ohlcv(rows: int = 488) -> str:
    start = date(2024, 1, 2)
    lines = [
        "# Stock data for FIXTURE",
        "Date,Open,High,Low,Close,Volume",
    ]
    for index in range(rows):
        current = start + timedelta(days=index)
        close = 100 + index * 0.25
        lines.append(
            f"{current.isoformat()},{close - 1:.2f},{close + 2:.2f},"
            f"{close - 2:.2f},{close:.2f},{1000000 + index * 1000}"
        )
    return attach_provenance(
        "\n".join(lines),
        ProvenanceRecord(
            evidence="get_stock_data",
            source="fixture",
            requested=f"{start.isoformat()} to {(start + timedelta(days=rows - 1)).isoformat()}",
            effective=f"{start.isoformat()} to {(start + timedelta(days=rows - 1)).isoformat()}",
            timing="market-date filtered",
        ),
    )


@pytest.mark.unit
def test_complete_ohlcv_is_kept_in_artifact_not_model_content() -> None:
    raw = _ohlcv()
    overview, artifact = build_market_data_artifact(
        raw,
        symbol="FIXTURE",
        start_date="2024-01-02",
        end_date="2026-07-28",
    )

    assert artifact["source_content"].count("\n") >= 488
    assert "2024-01-02,99.00,102.00,98.00,100.00,1000000" in artifact[
        "source_content"
    ]
    assert artifact["analytical_views"]["row_count"] == 488
    assert artifact["dataset_id"] in overview
    assert "complete source table is stored outside" in overview
    assert "2024-01-02,99.00,102.00,98.00,100.00,1000000" not in overview
    assert len(overview) < len(artifact["source_content"]) // 4


@pytest.mark.unit
def test_analytical_views_are_reproducible_and_cutoff_safe() -> None:
    frame = parse_ohlcv_frame(_ohlcv(40), cutoff="2024-01-31")
    views = market_analytical_views(
        frame,
        symbol="FIXTURE",
        requested_start="2024-01-02",
        requested_end="2024-01-31",
    )

    assert views["effective_end"] == "2024-01-31"
    assert views["row_count"] == 30
    assert views["latest"]["Close"] == 107.25
    assert views["returns"]["1_session"] == pytest.approx(
        107.25 / 107.0 - 1
    )
    assert views["drawdown"]["maximum"] == 0.0
    assert all(
        row["month"] == "2024-01"
        for row in views["monthly"]
    )


@pytest.mark.unit
def test_non_tabular_vendor_result_degrades_without_exposing_secrets() -> None:
    overview, artifact = build_market_data_artifact(
        "SAFE",
        symbol="NVDA",
        start_date="2019-12-01",
        end_date="2020-01-15",
    )

    assert artifact["source_content"] == "SAFE"
    assert artifact["analytical_views"]["status"] == "unavailable"
    assert artifact["analytical_views"]["row_count"] == 0
    assert "SAFE" not in overview
