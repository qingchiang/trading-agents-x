"""Typed source fixtures and checkpointed tool messages for offline tests."""

import re

from langchain_core.messages import ToolMessage

from tradingagents.data.evidence_workset import data_tool_output
from tradingagents.data.news_selection import news_result
from tradingagents.domain.data_result import DataDiagnostic, DataResult
from tradingagents.domain.news import NewsCandidate


def news_fixture(text: str, source: str = "") -> DataResult[str]:
    """Build a feed from readable test article literals, without transport markers."""
    parts = re.split(r"(?m)^### ", text)
    rows = []
    for body in parts[1:]:
        title = re.sub(r"^\[(?:direct|candidate|context)\]\s*", "", body.splitlines()[0])
        title = re.sub(r"\s*\((?:source|filer|institution):.*\)\s*$", "", title)
        published = re.search(r"(?m)^(?:Published|Disclosed|Submitted|Filed|Date):\s*(.+)$", body)
        link = re.search(r"(?m)^(?:Link|URL):\s*(.+)$", body)
        period = re.search(r"(?m)^Effective period:\s*(\d{4}-\d{2}-\d{2})", body)
        rows.append(
            NewsCandidate(
                source,
                title,
                "### " + body.strip(),
                published.group(1).strip() if published else None,
                link.group(1).strip() if link else "",
                effective_date=period.group(1) if period else None,
            )
        )
    diagnostics = (
        (DataDiagnostic("source_unavailable", source),)
        if "unavailable" in text.casefold() and not rows
        else ()
    )
    if "1 of 2" in text and "queries failed" in text:
        diagnostics += (
            DataDiagnostic("query_partial", source, "partial coverage; query_failures=1/2"),
        )
    return news_result(parts[0].strip(), rows, diagnostics=diagnostics)


def source_message(result: DataResult[str], **kwargs) -> ToolMessage:
    content, artifact = data_tool_output(result)
    return ToolMessage(content=content, artifact=artifact, **kwargs)


def news_source(content: str, *records) -> DataResult[str]:
    """Declare one producer's fixture articles with their source audit metadata."""
    from dataclasses import replace

    from tradingagents.data.news_selection import news_observations

    actual = [record for record in records if "unavailable" not in record.timing]
    if len(actual) != 1:
        return DataResult(content).with_provenance(*records)
    record = actual[0]
    feed = news_fixture(content, record.source)
    rows = tuple(replace(row, retrieved_at=record.retrieved_at) for row in feed.news)
    ticker = (
        "7203.T"
        if record.source in {"EDINET", "TDnet", "Google News"}
        else "600519.SS"
        if record.source in {"CNINFO", "Eastmoney Research"}
        else "NVDA"
    )
    observations = news_observations(rows, record.source, ticker)
    observations = tuple(replace(row, fallback="fallback" in record.timing) for row in observations)
    return replace(feed, news=rows, observations=observations).with_provenance(*records)


def replace_content(result: DataResult[str], old: str, new: str) -> DataResult[str]:
    from dataclasses import replace

    return replace(
        result,
        content=result.content.replace(old, new),
        market_data=market_fixture(result.content.replace(old, new)).market_data
        if result.market_data
        else None,
        provenance=tuple(
            replace(
                record,
                **{
                    key: value.replace(old, new)
                    for key, value in vars(record).items()
                    if isinstance(value, str)
                },
            )
            for record in result.provenance
        ),
    )


def source_route(result: DataResult[str]):
    """Serve a fixture only for the declared method, leaving optional context empty."""

    def fetch(method, *_args, **_kwargs):
        return (
            result
            if any(record.evidence == method for record in result.provenance)
            else DataResult("")
        )

    return fetch


def market_fixture(content: str) -> DataResult[str]:
    """Declare portable market rows from compact, readable test CSV literals."""
    import csv
    from io import StringIO

    from tradingagents.domain.data_result import MarketData

    match = re.search(r"^# Stock data for (.+?)(?: from |$)", content, re.MULTILINE)
    instrument = match.group(1) if match else "FIXTURE"
    header = content.casefold()
    basis = (
        "jquants_split_dividend_adjusted_close"
        if "j-quants split/dividend-adjusted close" in header
        else "yfinance_auto_adjusted_close"
        if "auto-adjusted" in header
        else "qfq_forward_adjusted"
        if "qfq (forward-adjusted)" in header
        else "unknown"
    )
    lines = content.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("Date,")), len(lines))
    rows = tuple(dict(row) for row in csv.DictReader(StringIO("\n".join(lines[start:]))))
    return DataResult(content, market_data=MarketData(instrument, basis, rows))


def scoped_fixture(result, scope):
    """Declare financial publication/period dates alongside readable fixture text."""
    from datetime import date

    available = re.findall(
        r"(?:disclosed |Latest visible disclosure/update: )(\d{4}-\d{2}-\d{2})", result.content
    )
    effective = re.search(
        r"(?:Effective period: |(?:FY|Q[1-4]) end )(\d{4}-\d{2}-\d{2})", result.content
    )
    for record in result.provenance:
        if "market-date filtered" in record.timing:
            available.extend(re.findall(r"\d{4}-\d{2}-\d{2}", record.effective))
        elif any(
            label in record.timing for label in ("publication", "disclosure-date")
        ) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", record.effective):
            available.append(record.effective)
    return result.with_scope(
        scope,
        available_on=date.fromisoformat(max(available)) if available else None,
        effective_date=date.fromisoformat(effective.group(1)) if effective else None,
    )
