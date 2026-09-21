"""Explicit source results shared by adapters and research consumers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any, TypedDict

from tradingagents.domain.data import (
    EvidenceSpan,
    ProvenanceRecord,
    SourceObservation,
    TemporalScopeName,
)
from tradingagents.domain.data_quality import temporal_scope_from_records
from tradingagents.domain.news import NewsCandidate


class StructuredNumericFact(TypedDict):
    """One producer-owned scalar carried outside model-visible prose."""

    key: str
    label: str
    value: int | float
    measurement_kind: str
    unit: str | None
    effective_date: str | None


@dataclass(frozen=True)
class DataDiagnostic:
    code: str
    source: str
    detail: str | None = None


@dataclass(frozen=True)
class DataResult[T]:
    """Source content and audit metadata travel together without text encoding."""

    content: T
    observations: tuple[SourceObservation, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()
    diagnostics: tuple[DataDiagnostic, ...] = ()
    spans: tuple[EvidenceSpan, ...] = ()
    numeric_facts: tuple[StructuredNumericFact, ...] = ()
    news: tuple[NewsCandidate, ...] = ()
    news_header: str | None = None

    def with_scope(self, scope: TemporalScopeName) -> DataResult:
        return replace(self, spans=(EvidenceSpan(self.content or None, self.provenance, scope),))

    def with_provenance(self, *records: ProvenanceRecord) -> DataResult:
        return replace(self, provenance=tuple(dict.fromkeys((*self.provenance, *records))))

    @classmethod
    def combine(cls, results: Iterable[DataResult], *, separator: str = "\n\n") -> DataResult:
        parts = tuple(results)
        return cls(
            content=separator.join(part.content for part in parts if part.content),
            observations=tuple(row for part in parts for row in part.observations),
            provenance=tuple(dict.fromkeys(record for part in parts for record in part.provenance)),
            diagnostics=tuple(issue for part in parts for issue in part.diagnostics),
            spans=tuple(
                span
                for part in parts
                for span in (
                    part.spans
                    or (
                        EvidenceSpan(
                            part.content or None,
                            part.provenance,
                            temporal_scope_from_records(part.provenance),
                        ),
                    )
                )
            ),
            numeric_facts=tuple(fact for part in parts for fact in part.numeric_facts),
            news=tuple(row for part in parts for row in part.news),
        )

    def dump(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "observations": [row.dump() for row in self.observations],
            "provenance": [asdict(record) for record in self.provenance],
            "diagnostics": [asdict(issue) for issue in self.diagnostics],
            "spans": [asdict(span) for span in self.spans],
            "numeric_facts": list(self.numeric_facts),
            "news": [asdict(row) for row in self.news],
            "news_header": self.news_header,
        }

    @classmethod
    def load(cls, payload: dict[str, Any]) -> DataResult:
        return cls(
            content=payload["content"],
            observations=tuple(SourceObservation.load(row) for row in payload["observations"]),
            provenance=tuple(ProvenanceRecord(**record) for record in payload["provenance"]),
            diagnostics=tuple(DataDiagnostic(**issue) for issue in payload["diagnostics"]),
            spans=tuple(
                EvidenceSpan(
                    span["content"],
                    tuple(ProvenanceRecord(**record) for record in span["records"]),
                    span["temporal_scope"],
                )
                for span in payload["spans"]
            ),
            numeric_facts=tuple(payload["numeric_facts"]),
            news=tuple(NewsCandidate(**row) for row in payload["news"]),
            news_header=payload["news_header"],
        )
