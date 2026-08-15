"""Canonical ordering helpers for public research reports."""

from __future__ import annotations

from collections.abc import Mapping

REPORT_ORDER = ("fundamentals", "market", "news", "social")


def order_reports[ReportValue](
    reports: Mapping[str, ReportValue],
) -> dict[str, ReportValue]:
    """Return reports in stable public order, followed by extension names."""
    ordered_names = [name for name in REPORT_ORDER if name in reports]
    ordered_names.extend(
        sorted(name for name in reports if name not in REPORT_ORDER)
    )
    return {name: reports[name] for name in ordered_names}
