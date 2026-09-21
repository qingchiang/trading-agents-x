"""Producer-owned news items before rendering or final prompt selection."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class NewsCandidate:
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
    effective_date: str | None = None

    @property
    def day(self) -> date | None:
        try:
            return date.fromisoformat(str(self.market_day or self.published)[:10])
        except ValueError:
            return None
