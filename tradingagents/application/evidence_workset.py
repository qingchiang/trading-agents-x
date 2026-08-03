"""Model-facing evidence views backed by complete, checkpointed tool artifacts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import asdict
from datetime import date
from typing import Any, Literal

import pandas as pd
from typing_extensions import NotRequired, TypedDict

from tradingagents.dataflows.measurement import instrument_currency
from tradingagents.provenance import (
    ProvenanceRecord,
    extract_provenance,
    strip_provenance_markers,
    temporal_scope_from_records,
)


class EvidenceToolArtifact(TypedDict):
    """JSON-safe complete tool output kept out of provider messages."""

    schema_version: Literal["1"]
    kind: Literal["source"]
    dataset_id: str
    evidence_type: str
    source_content: str
    provenance: list[dict[str, str | None]]
    temporal_scope: str
    analytical_views: dict[str, Any]
    column_measurements: NotRequired[dict[str, dict[str, str | None]]]
    structured_numeric_facts: NotRequired[list[dict[str, Any]]]


class StructuredNumericFact(TypedDict):
    """One producer-owned scalar carried outside model-visible prose."""

    key: str
    label: str
    value: int | float
    measurement_kind: str
    unit: str | None
    effective_date: str | None


_OHLCV_REQUIRED = {"date", "close"}
_OHLCV_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")
_RETURN_HORIZONS = (1, 5, 20, 60, 120, 252)
_VOLATILITY_HORIZONS = (20, 60, 252)


def build_market_data_artifact(
    raw: str,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
) -> tuple[str, EvidenceToolArtifact]:
    """Split a complete OHLCV result into a concise view and raw artifact."""

    source_content = strip_provenance_markers(raw).strip()
    records = tuple(extract_provenance(raw))
    frame = parse_ohlcv_frame(source_content, cutoff=end_date)
    views = market_analytical_views(
        frame,
        symbol=symbol,
        requested_start=start_date,
        requested_end=end_date,
    )
    dataset_id = "ds_" + hashlib.sha256(
        source_content.encode("utf-8")
    ).hexdigest()[:12]
    artifact: EvidenceToolArtifact = {
        "schema_version": "1",
        "kind": "source",
        "dataset_id": dataset_id,
        "evidence_type": "get_stock_data",
        "source_content": source_content,
        "provenance": [asdict(record) for record in records],
        "temporal_scope": temporal_scope_from_records(records),
        "analytical_views": views,
        "column_measurements": market_column_measurements(symbol),
    }
    return render_market_overview(dataset_id, views), artifact


def market_column_measurements(symbol: str) -> dict[str, dict[str, str | None]]:
    """Describe raw OHLCV columns using producer-owned instrument semantics."""

    currency = instrument_currency(symbol)
    return {
        column: {"measurement_kind": "currency", "unit": currency}
        for column in ("Open", "High", "Low", "Close", "Adj Close")
    } | {
        "Volume": {"measurement_kind": "quantity", "unit": "shares"},
    }


def is_evidence_tool_artifact(value: Any) -> bool:
    """Return whether a ToolMessage artifact follows the supported contract."""

    return (
        isinstance(value, dict)
        and value.get("schema_version") == "1"
        and value.get("kind") == "source"
        and isinstance(value.get("dataset_id"), str)
        and isinstance(value.get("source_content"), str)
        and isinstance(value.get("provenance"), list)
    )


def artifact_records(
    artifact: EvidenceToolArtifact | dict[str, Any],
) -> tuple[ProvenanceRecord, ...]:
    """Validate provenance records carried outside model-visible content."""

    records = []
    for raw in artifact.get("provenance", []):
        if not isinstance(raw, dict):
            continue
        try:
            records.append(
                ProvenanceRecord(
                    evidence=str(raw["evidence"]),
                    source=str(raw["source"]),
                    requested=str(raw.get("requested", "unknown")),
                    effective=str(raw.get("effective", "unknown")),
                    timing=str(raw.get("timing", "unknown")),
                    retrieved_at=(
                        str(raw["retrieved_at"])
                        if raw.get("retrieved_at") is not None
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(dict.fromkeys(records))


def parse_ohlcv_frame(content: str, *, cutoff: str) -> pd.DataFrame:
    """Parse the first OHLCV CSV block and fail closed past the cutoff."""

    lines = content.splitlines()
    header_index = None
    headers: list[str] = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith(("#", "<!--", "|")):
            continue
        try:
            candidate = [cell.strip() for cell in next(csv.reader([line]))]
        except (csv.Error, StopIteration):
            continue
        normalized = {cell.casefold() for cell in candidate}
        if _OHLCV_REQUIRED.issubset(normalized):
            header_index = index
            headers = candidate
            break
    if header_index is None:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)

    csv_lines = [",".join(headers)]
    for line in lines[header_index + 1 :]:
        if not line.strip() or line.lstrip().startswith(("#", "<!--", "|")):
            break
        try:
            cells = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            break
        if len(cells) != len(headers):
            break
        csv_lines.append(line)

    try:
        frame = pd.read_csv(io.StringIO("\n".join(csv_lines)))
    except (OSError, pd.errors.ParserError, ValueError):
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    date_column = next(
        (column for column in frame.columns if column.casefold() == "date"),
        None,
    )
    close_column = next(
        (column for column in frame.columns if column.casefold() == "close"),
        None,
    )
    if date_column is None or close_column is None:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)

    rename = {
        column: next(
            (
                canonical
                for canonical in _OHLCV_COLUMNS
                if column.casefold() == canonical.casefold()
            ),
            column,
        )
        for column in frame.columns
    }
    frame = frame.rename(columns=rename)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    cutoff_value = pd.Timestamp(date.fromisoformat(cutoff))
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Date", "Close"])
    frame = frame.loc[frame["Date"] <= cutoff_value]
    frame = frame.sort_values("Date").drop_duplicates("Date", keep="last")
    return frame.reset_index(drop=True)


def market_analytical_views(
    frame: pd.DataFrame,
    *,
    symbol: str,
    requested_start: str,
    requested_end: str,
) -> dict[str, Any]:
    """Create deterministic, compact views without discarding the raw table."""

    if frame.empty:
        return {
            "instrument": symbol,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "row_count": 0,
            "available_columns": [],
            "status": "unavailable",
        }

    close = frame["Close"].astype(float)
    returns = close.pct_change()
    latest = frame.iloc[-1]
    latest_date = _date_text(latest["Date"])
    horizon_returns = {
        f"{horizon}_session": _ratio_return(
            close.iloc[-1],
            close.iloc[-horizon - 1],
        )
        for horizon in _RETURN_HORIZONS
        if len(close) > horizon
    }
    current_year = frame.loc[frame["Date"].dt.year == latest["Date"].year]
    if len(current_year) > 1:
        horizon_returns["ytd"] = _ratio_return(
            close.iloc[-1],
            float(current_year.iloc[0]["Close"]),
        )
    if len(close) > 1:
        horizon_returns["full_window"] = _ratio_return(
            close.iloc[-1],
            close.iloc[0],
        )

    volatility = {}
    for horizon in _VOLATILITY_HORIZONS:
        material = returns.dropna().tail(horizon)
        if len(material) >= min(horizon, 5):
            volatility[f"{horizon}_session_annualized"] = _finite(
                float(material.std(ddof=1) * math.sqrt(252))
            )

    running_peak = close.cummax()
    drawdowns = close / running_peak - 1
    trough_index = drawdowns.idxmin()
    peak_index = close.loc[:trough_index].idxmax()
    current_peak_index = close.idxmax()
    current_drawdown = _finite(float(close.iloc[-1] / close.iloc[current_peak_index] - 1))

    trailing = frame.tail(min(252, len(frame)))
    trailing_high = (
        float(trailing["High"].max())
        if "High" in trailing and trailing["High"].notna().any()
        else float(trailing["Close"].max())
    )
    trailing_low = (
        float(trailing["Low"].min())
        if "Low" in trailing and trailing["Low"].notna().any()
        else float(trailing["Close"].min())
    )

    volume = frame.get("Volume")
    volume_views: dict[str, Any] = {}
    if volume is not None and volume.notna().any():
        latest_volume = float(volume.iloc[-1])
        for horizon in (20, 60):
            material = volume.dropna().tail(horizon)
            if not material.empty:
                average = float(material.mean())
                volume_views[f"{horizon}_session_average"] = _finite(average)
                volume_views[f"latest_vs_{horizon}_session_average"] = (
                    _finite(latest_volume / average)
                    if average
                    else None
                )
        volume_views["largest_sessions"] = _volume_anomalies(frame)

    return {
        "instrument": symbol,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "effective_start": _date_text(frame.iloc[0]["Date"]),
        "effective_end": latest_date,
        "row_count": int(len(frame)),
        "available_columns": [
            column for column in _OHLCV_COLUMNS if column in frame.columns
        ],
        "latest": {
            column: (
                latest_date
                if column == "Date"
                else _finite(float(latest[column]))
            )
            for column in _OHLCV_COLUMNS
            if column in frame.columns and pd.notna(latest[column])
        },
        "returns": horizon_returns,
        "realized_volatility": volatility,
        "drawdown": {
            "current": current_drawdown,
            "current_peak_date": _date_text(frame.loc[current_peak_index, "Date"]),
            "maximum": _finite(float(drawdowns.loc[trough_index])),
            "maximum_peak_date": _date_text(frame.loc[peak_index, "Date"]),
            "maximum_trough_date": _date_text(frame.loc[trough_index, "Date"]),
        },
        "trailing_52_week": {
            "high": _finite(trailing_high),
            "low": _finite(trailing_low),
            "distance_from_high": _ratio_return(close.iloc[-1], trailing_high),
            "distance_from_low": _ratio_return(close.iloc[-1], trailing_low),
        },
        "volume": volume_views,
        "largest_absolute_return_sessions": _return_anomalies(frame),
        "monthly": _monthly_view(frame),
        "data_quality": {
            "ordered_unique_dates": True,
            "analysis_cutoff_enforced": True,
        },
        "status": "available",
    }


def render_market_overview(
    dataset_id: str,
    views: dict[str, Any],
) -> str:
    """Render only compact analytical material for the tool conversation."""

    return (
        "# Market data analytical overview\n"
        f"Dataset ID: `{dataset_id}`\n"
        "The complete source table is retained outside the model conversation for "
        "audited, read-only access by later workflow stages.\n\n"
        + json.dumps(views, ensure_ascii=False, sort_keys=True)
    )


def _monthly_view(frame: pd.DataFrame) -> list[dict[str, Any]]:
    work = frame.copy()
    work["month"] = work["Date"].dt.to_period("M").astype(str)
    aggregations: dict[str, str] = {"Close": "last"}
    for column, function in (
        ("Open", "first"),
        ("High", "max"),
        ("Low", "min"),
        ("Volume", "sum"),
    ):
        if column in work:
            aggregations[column] = function
    monthly = work.groupby("month", sort=True).agg(aggregations).reset_index()
    monthly["return"] = monthly["Close"].pct_change()
    output = []
    for _, row in monthly.iterrows():
        output.append(
            {
                key: (
                    str(row[key])
                    if key == "month"
                    else _finite(float(row[key]))
                )
                for key in monthly.columns
                if pd.notna(row[key])
            }
        )
    return output


def _return_anomalies(frame: pd.DataFrame) -> list[dict[str, Any]]:
    material = frame[["Date", "Close"]].copy()
    material["return"] = material["Close"].pct_change()
    material = material.dropna(subset=["return"])
    material["magnitude"] = material["return"].abs()
    material = material.nlargest(5, "magnitude")
    return [
        {
            "date": _date_text(row["Date"]),
            "close": _finite(float(row["Close"])),
            "return": _finite(float(row["return"])),
        }
        for _, row in material.iterrows()
    ]


def _volume_anomalies(frame: pd.DataFrame) -> list[dict[str, Any]]:
    material = frame[["Date", "Volume"]].dropna(subset=["Volume"])
    material = material.nlargest(5, "Volume")
    return [
        {
            "date": _date_text(row["Date"]),
            "volume": _finite(float(row["Volume"])),
        }
        for _, row in material.iterrows()
    ]


def _ratio_return(last: Any, first: Any) -> float | None:
    first_value = float(first)
    if not first_value:
        return None
    return _finite(float(last) / first_value - 1)


def _finite(value: float) -> float | None:
    return round(value, 10) if math.isfinite(value) else None


def _date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()
