"""Convert adapter frames to portable market rows without parsing report text."""

import pandas as pd

from tradingagents.domain.data import scalar
from tradingagents.domain.data_result import MarketData


def market_data(frame: pd.DataFrame, instrument: str, adjustment_basis: str) -> MarketData:
    output = frame.copy()
    if "Date" not in output:
        output = output.reset_index().rename(columns={output.index.name or "index": "Date"})
    output["Date"] = pd.to_datetime(output["Date"]).dt.strftime("%Y-%m-%d")
    return MarketData(
        instrument, adjustment_basis, tuple(scalar(row) for row in output.to_dict("records"))
    )
