"""Local state for one evidence-collection analyst subgraph."""

from collections.abc import Iterable
from dataclasses import asdict
from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import NotRequired, TypedDict

from tradingagents.agents.utils.information_frontier import source_metadata
from tradingagents.application.evidence_workset import StructuredNumericFact
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
    dataset_id: NotRequired[str]
    structured_numeric_facts: NotRequired[list[StructuredNumericFact]]
    source_records: NotRequired[list[dict[str, object]]]
    source_watermarks: NotRequired[list[dict[str, object]]]


class AgentState(MessagesState):
    company_of_interest: Annotated[str, "Canonical instrument symbol"]
    asset_type: Annotated[str, "stock"]
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
    information_frontier: Annotated[
        str | None,
        "Frozen timezone-aware point-in-time boundary for source collection",
    ]


def prefetched_evidence_block(
    body: str,
    records: Iterable[ProvenanceRecord],
    *,
    temporal_scope: str | None = None,
    dataset_id: str | None = None,
    structured_numeric_facts: Iterable[StructuredNumericFact] = (),
) -> PrefetchedEvidenceBlock:
    """Serialize one prefetch response independently from report rendering."""

    records = tuple(records)
    content = strip_provenance_markers(body).strip()
    metadata = source_metadata(body)
    unavailable = (
        records
        and all(
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
    ) or (
        bool(metadata.get("source_watermarks"))
        and all(
            item.get("status") == "unavailable"
            for item in metadata["source_watermarks"]
        )
    )
    if (
        not content
        or unavailable
        or (content.startswith("<") and content.endswith(">"))
    ):
        content = None
    block: PrefetchedEvidenceBlock = {
        "content": content,
        "records": [asdict(record) for record in records],
        "temporal_scope": (
            temporal_scope
            if temporal_scope
            in {"point_in_time", "live_only", "unknown"}
            else temporal_scope_from_records(records)
        ),
    }
    if dataset_id:
        block["dataset_id"] = dataset_id
    facts = list(structured_numeric_facts)
    if facts:
        block["structured_numeric_facts"] = facts
    block.update(metadata)
    return block


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
