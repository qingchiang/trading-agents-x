"""Canonical ordering helpers for public research reports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

REPORT_ORDER = ("fundamentals", "market", "news", "social")

_ReportValue = TypeVar("_ReportValue")


def order_reports(
    reports: Mapping[str, _ReportValue],
) -> dict[str, _ReportValue]:
    """Return reports in the stable public order, followed by legacy names."""
    ordered_names = [name for name in REPORT_ORDER if name in reports]
    ordered_names.extend(
        sorted(name for name in reports if name not in REPORT_ORDER)
    )
    return {name: reports[name] for name in ordered_names}
