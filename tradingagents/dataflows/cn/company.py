"""Bounded CNINFO company-profile access and mainland entity classification."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

import pandas as pd
import requests

from .common import (
    REQUEST_TIMEOUT,
    AkShareSchemaError,
    AkShareUnavailableError,
    call_with_retry,
    canonical_a_share,
    load_akshare,
)

_PROFILE_URL = "https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1133"
_PROFILE_FIELD_MAP = (
    ("ORGNAME", "公司名称"),
    ("F001V", "英文名称"),
    ("F002V", "曾用简称"),
    ("ASECCODE", "A股代码"),
    ("ASECNAME", "A股简称"),
    ("BSECCODE", "B股代码"),
    ("BSECNAME", "B股简称"),
    ("HSECCODE", "H股代码"),
    ("HSECNAME", "H股简称"),
    ("F044V", "入选指数"),
    ("MARKET", "所属市场"),
    ("F032V", "所属行业"),
    ("F003V", "法人代表"),
    ("F007N", "注册资金"),
    ("F010D", "成立日期"),
    ("F006D", "上市日期"),
    ("F011V", "官方网站"),
    ("F012V", "电子邮箱"),
    ("F013V", "联系电话"),
    ("F014V", "传真"),
    ("F004V", "注册地址"),
    ("F005V", "办公地址"),
    ("F006V", "邮政编码"),
    ("F015V", "主营业务"),
    ("F016V", "经营范围"),
    ("F017V", "机构简介"),
)
_PROFILE_COLUMNS = tuple(label for _key, label in _PROFILE_FIELD_MAP)
_FINANCIAL_TOKENS = (
    "银行",
    "证券",
    "保险",
    "信托",
    "金融服务",
    "多元金融",
    "贷款及垫款",
    "吸收存款",
    "保费收入",
    "手续费及佣金净收入",
)


def clear_cache() -> None:
    """Clear the successful company-profile cache (primarily for tests)."""
    _profile_by_code.cache_clear()


def _cninfo_headers() -> dict[str, str]:
    """Build CNINFO's short-lived request header without importing AkShare eagerly."""
    load_akshare()
    try:
        from akshare.stock.stock_profile_cninfo import _get_file_content_ths
        from py_mini_racer import py_mini_racer
    except Exception as exc:  # noqa: BLE001 - dependency layout varies by platform
        raise AkShareUnavailableError(
            f"AkShare CNINFO decoder is unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        js = py_mini_racer.MiniRacer()
        js.eval(_get_file_content_ths("cninfo.js"))
        mcode = js.call("getResCode1")
    except Exception as exc:  # noqa: BLE001 - JS runtime failures vary
        raise AkShareUnavailableError(
            f"AkShare CNINFO request signing failed: {type(exc).__name__}: {exc}"
        ) from exc
    return {
        "Accept": "*/*",
        "Accept-Enckey": str(mcode),
        "Origin": "https://webapi.cninfo.com.cn",
        "Referer": "https://webapi.cninfo.com.cn/",
        "X-Requested-With": "XMLHttpRequest",
    }


@lru_cache(maxsize=128)
def _profile_by_code(code: str) -> pd.DataFrame:
    headers = _cninfo_headers()

    def request_profile():
        response = requests.post(
            _PROFILE_URL,
            params={"scode": code},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    payload = call_with_retry(
        request_profile,
        label="AkShare/CNINFO stock_profile_cninfo",
    )
    try:
        count = int(payload.get("count", 0))
        records = payload.get("records") or []
    except (AttributeError, TypeError, ValueError) as exc:
        raise AkShareSchemaError(
            f"CNINFO company-profile response has an invalid envelope: {exc}"
        ) from exc
    if count == 0:
        return pd.DataFrame(columns=_PROFILE_COLUMNS)
    if count != 1 or not records or not isinstance(records[0], dict):
        raise AkShareSchemaError(
            f"CNINFO company-profile response expected one record, got count={count}."
        )
    record = records[0]
    missing_keys = [key for key, _label in _PROFILE_FIELD_MAP if key not in record]
    if missing_keys:
        raise AkShareSchemaError(
            "CNINFO company-profile record changed schema: "
            f"missing required key(s) {missing_keys}."
        )
    values = [record[key] for key, _label in _PROFILE_FIELD_MAP]
    return pd.DataFrame([values], columns=_PROFILE_COLUMNS)


def get_company_profile(symbol: str) -> pd.DataFrame:
    """Return a defensive copy of the CNINFO company profile for an A-share."""
    _canonical, code, _exchange = canonical_a_share(symbol)
    return _profile_by_code(code).copy()


def classify_entity(
    profile: pd.DataFrame | None = None,
    field_names: Iterable[object] = (),
) -> str:
    """Return ``financial`` for banks/brokers/insurers, otherwise ``general``."""
    text_parts: list[str] = []
    if profile is not None and not profile.empty:
        for column in ("所属行业", "主营业务", "经营范围", "机构简介"):
            if column in profile.columns:
                text_parts.extend(profile[column].dropna().astype(str).tolist())
    else:
        # Sina exposes a superset template whose columns may include bank-only
        # labels even for manufacturers. Use labels only when CNINFO is absent.
        text_parts.extend(str(value) for value in field_names if value is not None)
    text = " ".join(text_parts)
    return "financial" if any(token in text for token in _FINANCIAL_TOKENS) else "general"
