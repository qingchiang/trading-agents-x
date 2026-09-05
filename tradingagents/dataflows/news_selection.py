"""Bounded candidate selection shared by ticker-news producers and assemblers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

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


def publication_instant(value) -> datetime:
    """Order reliable timestamps without discarding their intraday precision."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(re.sub(r"\s+(?:JST|CST)$", "", value).replace("Z", "+00:00"))
        except ValueError:
            value = publication_day(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def select_temporal[T](
    rows: list[T],
    limit: int,
    start_date: str,
    end_date: str,
    *,
    published: Callable[[T], date | datetime | str | None],
    dated: Callable[[T], date | None] | None = None,
) -> list[T]:
    """Reserve one third for three earlier time bands, borrowing unused slots."""
    if limit <= 0:
        return []
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: publication_instant(published(pair[1])),
        reverse=True,
    )
    if len(rows) <= limit:
        return [row for _, row in ordered]
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    recent_start = max(start, end - timedelta(days=6))
    day = dated or (lambda row: publication_day(published(row)))
    recent = [
        pair
        for pair in ordered
        if (day(pair[1]) or date.min) >= recent_start
    ]
    older = [
        pair
        for pair in ordered
        if start <= (day(pair[1]) or date.min) < recent_start
    ]
    reserved = limit // 3 if older else 0
    chosen = recent[: limit - reserved]
    width = max(1, (recent_start - start).days)
    buckets = [[], [], []]
    for pair in older:
        offset = (day(pair[1]) - start).days
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
    market_day: str | None = None
    refresh_failure: str | None = None

    @classmethod
    def from_item(cls, source: str, item: str) -> NewsCandidate:
        metadata = re.search(r"<!-- news-observation: (.*?) -->", item)
        if metadata:
            fields = json.loads(metadata[1])
            item = re.sub(r"\nObservation: retrieved[^\n]*", "", item[:metadata.start()]).rstrip()
            return cls(content=item, **fields)
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
        return publication_day(self.market_day or self.published)


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
            select_temporal(rows, budgets[i], start_date, end_date,
                            published=lambda r: r.retrieved_at if r.revision else r.published,
                            dated=lambda r: r.day)
            if start_date and end_date
            else rows[: budgets[i]]
        )
        counts.append(MergeCounts(returned[i], duplicates[i], len(kept), len(rows) - len(kept)))
        if kept:
            merged.append(header + "\n\n" + "\n\n".join(render_candidate(row) for row in kept))
    return merged, counts


def render_candidate(row: NewsCandidate) -> str:
    if not row.retrieved_at:
        return row.content
    metadata = asdict(row)
    metadata.pop("content")
    timing = "; near-live revision; revision publication unknown" if row.revision else ""
    return row.content + f"\nObservation: retrieved {row.retrieved_at}{timing}" + "\n<!-- news-observation: " + json.dumps(metadata, ensure_ascii=False) + " -->"


def emit_news(block: str, source: str, ticker: str, *, global_news=False) -> None:
    from .source_observations import publish_observation
    from .symbol_utils import market_timezone

    for row in split_candidates(block, source)[1]:
        stamp = None
        try:
            if len(row.published) <= 10:
                raise ValueError("date-only publication")
            stamp = datetime.fromisoformat(row.published.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=market_timezone(ticker))
        except (ValueError, TypeError, AttributeError):
            pass
        timing = ("near-live revision; revision publication unknown" if row.revision else
                  "publication dated; bounded news selection" if row.day else
                  "near-live undated news; publication time unknown")
        if row.refresh_failure:
            timing += f"; news cache refresh failed: {row.refresh_failure}; retained cached material"
        publish_observation(
            row.source or source, "global_news_article" if global_news else "news_article",
            row.record_id or row.link or row.title,
            {"title": row.title, "content": row.content, "link": row.link,
             "original_publication": row.published, "revision": row.revision},
            effective_date=row.day,
            available_at=None if row.revision else stamp,
            available_on=None if row.revision or stamp else row.day,
            retrieved_at=datetime.fromisoformat(row.retrieved_at) if row.retrieved_at else datetime.now(UTC),
            timing=timing,
        )


def finalize_news(block, source, ticker, start, end, limit, *, global_news=False):
    header, rows = split_candidates(block, source)
    if not rows:
        return block
    merged, counts = merge_news_blocks([block], limit, start, end)
    result = merged[0] if merged else header
    count = counts[0]
    tiers = {tier: len(re.findall(r"(?m)^### \[" + tier + r"\]", result)) for tier in ("direct", "candidate", "context")}
    result = re.sub(r"kept=\d+ \(direct=\d+, candidate=\d+, context=\d+\)",
                    f"kept={count.kept} (direct={tiers['direct']}, candidate={tiers['candidate']}, context={tiers['context']})", result)
    result = re.sub(r"omitted_by_limit=\d+", f"omitted_by_limit={count.cap_omitted}", result)
    emit_news(result, source, ticker, global_news=global_news)
    result += f"\nSelection: duplicates={count.duplicates}; final={count.kept}; truncated={count.cap_omitted}."
    return result
