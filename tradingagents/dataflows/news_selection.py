"""Bounded candidate selection shared by ticker-news producers and assemblers."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import get_config
from .news_quality import canonical_headline

_candidate_mode: ContextVar[bool] = ContextVar("news_candidate_mode", default=False)


@contextmanager
def candidate_scope() -> Iterator[None]:
    token = _candidate_mode.set(True)
    try:
        yield
    finally:
        _candidate_mode.reset(token)


def source_output_limit(default: int | None = None) -> int:
    if _candidate_mode.get():
        return 100
    return max(1, int(default if default is not None else get_config()["news_article_limit"]))


def in_candidate_scope() -> bool:
    return _candidate_mode.get()


def publication_day(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def select_temporal[T](
    rows: list[T],
    limit: int,
    start_date: str,
    end_date: str,
    *,
    published: Callable[[T], date | datetime | None],
) -> list[T]:
    """Reserve one third for three earlier time bands, borrowing unused slots."""
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return list(rows)
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    recent_start = max(start, end - timedelta(days=6))
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: publication_day(published(pair[1])) or date.min,
        reverse=True,
    )
    recent = [
        pair
        for pair in ordered
        if (publication_day(published(pair[1])) or date.min) >= recent_start
    ]
    older = [
        pair
        for pair in ordered
        if start <= (publication_day(published(pair[1])) or date.min) < recent_start
    ]
    reserved = limit // 3 if older else 0
    chosen = recent[: limit - reserved]
    width = max(1, (recent_start - start).days)
    buckets = [[], [], []]
    for pair in older:
        offset = (publication_day(published(pair[1])) - start).days
        buckets[min(2, offset * 3 // width)].append(pair)
    for _ in range(reserved):
        for bucket in buckets:
            if bucket:
                chosen.append(bucket.pop(0))
                break
        buckets = buckets[1:] + buckets[:1]
    seen = {index for index, _ in chosen}
    for pair in ordered:
        if len(chosen) >= limit:
            break
        if pair[0] not in seen:
            chosen.append(pair)
            seen.add(pair[0])
    return [row for _, row in chosen]


@dataclass(frozen=True)
class NewsCandidate:
    """One source item before the final model budget is applied."""

    source: str
    title: str
    content: str
    published: str | None
    link: str = ""
    record_id: str = ""
    retrieved_at: str | None = None
    revision: bool = False

    @classmethod
    def from_item(cls, source: str, item: str) -> NewsCandidate:
        title = item.splitlines()[0].removeprefix("### ").strip()
        title = re.sub(r"^\[(?:direct|candidate|context)\]\s*", "", title)
        title = re.sub(r"\s+\((?:source|filer|institution):.*\)\s*$", "", title)
        stamp = re.search(
            r"(?m)^(?:(?:Submitted|Disclosed|Published):\s*)?(\d{4}-\d{2}-\d{2}[^\n·]*)", item
        )
        link = re.search(r"https?://[^\s<>]+", item)
        doc = re.search(r"EDINET docID:\s*(\w+)", item)
        return cls(
            source,
            title,
            item.rstrip(),
            stamp[1].strip() if stamp else None,
            link[0] if link else "",
            doc[1] if doc else "",
        )

    @property
    def day(self) -> date | None:
        return publication_day(self.published)


@dataclass(frozen=True)
class MergeCounts:
    returned: int
    duplicates: int
    kept: int
    cap_omitted: int


def split_candidates(block: str, source: str = "") -> tuple[str, list[NewsCandidate]]:
    starts = [match.start() for match in re.finditer(r"(?m)^### ", block)]
    if not starts:
        return block.rstrip(), []
    return block[: starts[0]].rstrip(), [
        NewsCandidate.from_item(
            source, block[start : starts[i + 1] if i + 1 < len(starts) else None]
        )
        for i, start in enumerate(starts)
    ]


def merge_news_blocks(blocks, limit, start_date=None, end_date=None, quotas=None):
    seen = set()
    parsed = []
    duplicates = []
    returned = []
    for block in blocks:
        header, candidates = split_candidates(block)
        returned.append(len(candidates))
        unique = []
        omitted = 0
        for candidate in candidates:
            keys = {("title", canonical_headline(candidate.title), str(candidate.day or ""))}
            if candidate.link:
                keys.add(("url", candidate.link))
            if candidate.record_id:
                keys.add(("record", candidate.record_id))
            if seen.intersection(keys):
                omitted += 1
                continue
            seen.update(keys)
            unique.append(candidate)
        parsed.append((header, unique))
        duplicates.append(omitted)
    budgets = [0] * len(parsed)
    if quotas is not None:
        budgets = [min(len(rows), quota) for (_, rows), quota in zip(parsed, quotas, strict=True)]
    remaining = max(0, limit - sum(budgets))
    for i, (_, rows) in enumerate(parsed):
        extra = min(remaining, len(rows) - budgets[i])
        budgets[i] += extra
        remaining -= extra
    merged, counts = [], []
    for i, (header, rows) in enumerate(parsed):
        kept = (
            select_temporal(rows, budgets[i], start_date, end_date, published=lambda r: r.day)
            if start_date and end_date
            else rows[: budgets[i]]
        )
        counts.append(MergeCounts(returned[i], duplicates[i], len(kept), len(rows) - len(kept)))
        if kept:
            merged.append(header + "\n\n" + "\n\n".join(row.content for row in kept))
    return merged, counts
