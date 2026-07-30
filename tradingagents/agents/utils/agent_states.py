"""Local state for one evidence-collection analyst subgraph."""

from collections.abc import Iterable
from dataclasses import asdict
from typing import Annotated, TypedDict

from langgraph.graph import MessagesState

from tradingagents.provenance import (
    ProvenanceRecord,
    strip_provenance_markers,
    temporal_scope_from_records,
)


class PrefetchedEvidenceBlock(TypedDict):
    """JSON-safe evidence transported by analysts that prefetch their inputs."""

    content: str | None
    records: list[dict[str, str | None]]
    temporal_scope: str


class AgentState(MessagesState):
    company_of_interest: Annotated[str, "Canonical instrument symbol"]
    asset_type: Annotated[str, "stock or crypto"]
    instrument_context: Annotated[str, "Identity resolved once at run start"]
    trade_date: Annotated[str, "Immutable point-in-time analysis cutoff"]
    past_context: Annotated[str, "Deterministically selected reflection context"]
    market_report: Annotated[str, "Market analyst narrative"]
    sentiment_report: Annotated[str, "Sentiment analyst narrative"]
    news_report: Annotated[str, "News analyst narrative"]
    fundamentals_report: Annotated[str, "Fundamentals analyst narrative"]
    sentiment_confidence: Annotated[
        float | None,
        "Locally calculated confidence shared with the research graph",
    ]
    prefetched_evidence: Annotated[
        list[PrefetchedEvidenceBlock],
        "Evidence fetched before an analyst LLM call",
    ]


def prefetched_evidence_block(
    body: str,
    records: Iterable[ProvenanceRecord],
    *,
    temporal_scope: str | None = None,
) -> PrefetchedEvidenceBlock:
    """Serialize one prefetch response independently from report rendering."""

    records = tuple(records)
    content = strip_provenance_markers(body).strip()
    unavailable = records and all(
        any(
            token in record.timing.casefold()
            for token in (
                "unavailable",
                "failed",
                "not queried",
                "no usable data",
            )
        )
        for record in records
    )
    if (
        not content
        or unavailable
        or (content.startswith("<") and content.endswith(">"))
    ):
        content = None
    return {
        "content": content,
        "records": [asdict(record) for record in records],
        "temporal_scope": (
            temporal_scope
            if temporal_scope
            in {"point_in_time", "live_only", "unknown"}
            else temporal_scope_from_records(records)
        ),
    }


def missing_evidence_blocks(
    records: Iterable[ProvenanceRecord],
    expected: Iterable[tuple[str, str]],
    *,
    requested_date: str,
) -> list[PrefetchedEvidenceBlock]:
    """Represent expected tool evidence that was never requested."""

    present = {record.evidence for record in records}
    return [
        prefetched_evidence_block(
            "",
            (
                ProvenanceRecord(
                    evidence=label,
                    source="—",
                    requested=requested_date,
                    effective="—",
                    timing="not requested",
                ),
            ),
        )
        for evidence, label in expected
        if evidence not in present
    ]
