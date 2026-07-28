"""Canonical ordering helpers for public research reports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TypeVar

REPORT_ORDER = ("fundamentals", "market", "news", "social")

_ReportValue = TypeVar("_ReportValue")
_LEGACY_AUDIT_BLOCK_RE = re.compile(
    r"<!--\s*tradingagents-data-provenance:start\s*-->"
    r".*?"
    r"<!--\s*tradingagents-data-provenance:end\s*-->",
    re.DOTALL | re.IGNORECASE,
)
_LEGACY_AUDIT_HEADING_RE = re.compile(
    r"(?m)^\s*##\s+Data\s+(?:Quality\s+Warnings|Provenance)\s*$",
    re.IGNORECASE,
)


def order_reports(
    reports: Mapping[str, _ReportValue],
) -> dict[str, _ReportValue]:
    """Return reports in the stable public order, followed by legacy names."""
    ordered_names = [name for name in REPORT_ORDER if name in reports]
    ordered_names.extend(
        sorted(name for name in reports if name not in REPORT_ORDER)
    )
    return {name: reports[name] for name in ordered_names}


def strip_report_audit_sections(value: str) -> str:
    """Remove generated legacy warning/provenance appendices from a narrative.

    Historical rows remain byte-for-byte unchanged in the database. This helper
    is deliberately applied only at current graph, Web, and Markdown-export
    presentation boundaries.
    """

    text = value if isinstance(value, str) else str(value)
    text = _LEGACY_AUDIT_BLOCK_RE.sub("", text)
    heading = _LEGACY_AUDIT_HEADING_RE.search(text)
    if heading is not None:
        text = text[: heading.start()]
    text = text.rstrip()
    if text.endswith("---"):
        text = text[:-3].rstrip()
    return text
