"""Best-effort market-local instrument names from current metadata sources."""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradingagents.dataflows.cn.company import get_company_profile
from tradingagents.dataflows.jp.company_info import get_company_name
from tradingagents.dataflows.symbol_utils import match_exchange_suffix

_JP_NAME_VENDORS = frozenset(
    {"jquants", "jp_fundamentals", "jp_news", "jp_statements"}
)
_CN_NAME_VENDORS = frozenset(
    {"akshare", "cn_fundamentals", "cn_news", "cn_statements"}
)


def resolve_local_instrument_name(
    ticker: str,
    analysis_date: str,
    config: dict[str, Any],
) -> str | None:
    """Resolve a configured market-local name as current display metadata."""
    del analysis_date  # Names intentionally follow live metadata semantics.
    routes = config.get("data_vendors_by_market", {})
    if not isinstance(routes, dict):
        return None
    suffix = match_exchange_suffix(ticker, routes)
    route = routes.get(suffix, {})
    if not isinstance(route, dict):
        return None
    vendors = _configured_vendors(route)
    if suffix == ".T" and vendors & _JP_NAME_VENDORS:
        return _clean_name(get_company_name(ticker))
    if suffix in {".SS", ".SZ"} and vendors & _CN_NAME_VENDORS:
        profile = get_company_profile(ticker)
        if profile.empty:
            return None
        value = profile.iloc[0].get("A股简称")
        return None if pd.isna(value) else _clean_name(value)
    return None


def _configured_vendors(route: dict[str, Any]) -> set[str]:
    vendors: set[str] = set()
    for chain in route.values():
        if isinstance(chain, str):
            vendors.update(item.strip() for item in chain.split(",") if item.strip())
    return vendors


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned[:300]
