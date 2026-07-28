"""Local state for one evidence-collection analyst subgraph."""

from typing import Annotated, TypedDict

from langgraph.graph import MessagesState


class PrefetchedEvidenceBlock(TypedDict):
    """JSON-safe evidence transported by analysts that prefetch their inputs."""

    content: str | None
    records: list[dict[str, str | None]]


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
    prefetched_evidence: Annotated[
        list[PrefetchedEvidenceBlock],
        "Evidence fetched before an analyst LLM call",
    ]
