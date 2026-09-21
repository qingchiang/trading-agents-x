"""Shared offline fixtures for china financials contracts."""

import pandas as pd


def _frame(*, bank: bool = False) -> pd.DataFrame:
    data = {
        "ReportDate": pd.to_datetime(["2025-12-31", "2025-09-30"]),
        "PublishDate": pd.to_datetime(["2026-03-20", "2025-10-25"]),
        "UpdateDate": pd.to_datetime(["2026-03-21", "2025-10-26"]),
        "VisibilityDate": pd.to_datetime(["2026-03-21", "2025-10-26"]),
        "Currency": ["CNY", "CNY"],
        "Audited": ["yes", "no"],
        "营业收入": [1000, 700],
        "净利润": [100, 70],
        "资产总计": [5000, 4800],
        "负债合计": [3000, 2900],
        "经营活动产生的现金流量净额": [200, 120],
    }
    if bank:
        data.update(
            {
                "利息净收入": [500, 350],
                "吸收存款": [3500, 3400],
                "发放贷款及垫款": [3200, 3100],
                "客户存款和同业存放款项净增加额": [80, 60],
            }
        )
    return pd.DataFrame(data)
