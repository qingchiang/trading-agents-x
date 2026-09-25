"""Producer-side news counts, before cache merging and final selection."""

from dataclasses import asdict, dataclass

from tradingagents.domain.data_result import DataDiagnostic


@dataclass
class CandidateFilterCounts:
    upstream_returned: int = 0
    date_filtered: int = 0
    relevance_filtered: int = 0
    invalid_records: int = 0
    duplicates: int = 0
    source_truncated: int = 0

    def restore(self, other: "CandidateFilterCounts") -> None:
        """Restore cached upstream counts before applying this request's window."""
        for key, value in asdict(other).items():
            setattr(self, key, value)

    def render(self) -> str:
        counts = asdict(self)
        candidates = self.upstream_returned - sum(
            value for key, value in counts.items() if key != "upstream_returned"
        )
        return (
            "Candidate filter: "
            + "; ".join(f"{key}={value}" for key, value in counts.items())
            + f"; candidates={candidates}."
        )

    def diagnostic(self, source: str) -> DataDiagnostic:
        return DataDiagnostic("candidate_filter", source, self.render())
