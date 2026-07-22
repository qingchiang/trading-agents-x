"""Shanghai/Shenzhen trading calendar with bounded network access."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .common import REQUEST_TIMEOUT, AkShareSchemaError, call_with_retry, load_akshare

_CALENDAR_URL = "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DAILY_BAR_SETTLE_TIME = time(15, 30)


@lru_cache(maxsize=1)
def trading_dates() -> tuple[date, ...]:
    """Return the AkShare/Sina mainland exchange calendar, sorted and cached."""
    # Lazy AkShare import is intentional: importing its stock constants can fail
    # when a runtime dependency is damaged, which must remain a typed vendor error.
    load_akshare()
    try:
        from akshare.stock.cons import hk_js_decode
        from py_mini_racer import py_mini_racer
    except Exception as exc:  # noqa: BLE001 - dependency-specific failures vary
        from .common import AkShareUnavailableError

        raise AkShareUnavailableError(
            f"AkShare trading-calendar decoder is unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    def request_calendar():
        response = requests.get(_CALENDAR_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response

    response = call_with_retry(
        request_calendar,
        label="AkShare/Sina trading calendar",
    )
    try:
        payload = response.text.split("=", 1)[1].split(";", 1)[0].replace('"', "")
        js = py_mini_racer.MiniRacer()
        js.eval(hk_js_decode)
        decoded = js.call("d", payload)
        parsed = pd.to_datetime(pd.Series(decoded), errors="coerce").dropna()
    except Exception as exc:  # noqa: BLE001 - upstream format/decoder failures vary
        raise AkShareSchemaError(
            f"AkShare/Sina trading calendar response could not be decoded: {exc}"
        ) from exc
    dates = tuple(sorted(set(parsed.dt.date)))
    if not dates:
        raise AkShareSchemaError("AkShare/Sina trading calendar returned no dates.")
    return dates


def previous_trade_date(value: str | date | datetime, *, inclusive: bool = True) -> date:
    """Return the latest mainland trading date on/before ``value``."""
    target = pd.Timestamp(value).date()
    dates = trading_dates()
    index = bisect_right(dates, target) - 1
    if not inclusive and index >= 0 and dates[index] == target:
        index -= 1
    if index < 0:
        raise ValueError(f"No mainland China trading date found before {target}.")
    if target > dates[-1]:
        raise AkShareSchemaError(
            f"Trading calendar ends at {dates[-1]}, before requested date {target}."
        )
    return dates[index]


def is_trade_date(value: str | date | datetime) -> bool:
    """True when ``value`` is an SSE/SZSE trading date."""
    target = pd.Timestamp(value).date()
    dates = trading_dates()
    index = bisect_right(dates, target) - 1
    return index >= 0 and dates[index] == target


def effective_trade_date(
    value: str | date | datetime,
    *,
    now: datetime | None = None,
) -> date:
    """Return the latest completed mainland daily-bar date for a request.

    Before 15:30 Asia/Shanghai on a live trading day, the current daily candle
    is incomplete (and some sources still expose only yesterday), so low-frequency
    analysis closes on the prior session. Historical and post-settlement requests
    use the inclusive exchange calendar normally.
    """
    target = pd.Timestamp(value).date()
    current = now or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SHANGHAI)
    else:
        current = current.astimezone(_SHANGHAI)
    if (
        target == current.date()
        and current.time() < _DAILY_BAR_SETTLE_TIME
        and is_trade_date(target)
    ):
        return previous_trade_date(target, inclusive=False)
    return previous_trade_date(target)
