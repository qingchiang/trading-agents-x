"""Indexed Required Source Coverage evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from tradingagents.dataflows.symbol_utils import market_timezone

from ._research_models import (
    CoverageStatus,
    ResearchRevisionDraft,
    ResearchRevisionRole,
    SourceRecordVersion,
    SourceWatermarkSnapshot,
)
from .research_intervals import DateInterval, DateIntervalSet


@dataclass(frozen=True)
class RequiredSourceCoverageIndex:
    """Indexes a Revision snapshot once and evaluates all Required sources."""

    revision: ResearchRevisionDraft
    evidence_refs: frozenset[str]
    lineage_version_ids: frozenset[str]
    records_by_source: dict[str, tuple[SourceRecordVersion, ...]]
    watermarks_by_source: dict[str, tuple[SourceWatermarkSnapshot, ...]]

    @classmethod
    def build(cls, revision: ResearchRevisionDraft) -> RequiredSourceCoverageIndex:
        records: dict[str, list[SourceRecordVersion]] = {}
        for record in revision.evidence_snapshot.source_records:
            records.setdefault(record.source, []).append(record)
        watermarks: dict[str, list[SourceWatermarkSnapshot]] = {}
        for watermark in revision.evidence_snapshot.source_watermarks:
            watermarks.setdefault(watermark.source, []).append(watermark)
        return cls(
            revision=revision,
            evidence_refs=frozenset(item.ref for item in revision.evidence_snapshot.bundle.items),
            lineage_version_ids=frozenset(
                item.version_id
                for item in revision.evidence_snapshot.source_record_lineage
                if item.observed_in_execution
            ),
            records_by_source={key: tuple(value) for key, value in records.items()},
            watermarks_by_source={key: tuple(value) for key, value in watermarks.items()},
        )

    def complete(self, required_sources: tuple[str, ...]) -> bool:
        revision = self.revision
        market_tz = market_timezone(revision.current_state.instrument)
        if any(
            record.evidence_ref not in self.evidence_refs
            or record.available_at.astimezone(market_tz).date() > revision.cutoff
            for records in self.records_by_source.values()
            for record in records
        ):
            return False
        return all(self._source_complete(source) for source in required_sources)

    def _source_complete(self, source: str) -> bool:
        revision = self.revision
        watermarks = self.watermarks_by_source.get(source, ())
        applicable = tuple(
            item
            for item in watermarks
            if item.scanned_start <= revision.cutoff <= item.scanned_end
            and item.scanned_end == revision.cutoff
            and item.status is CoverageStatus.COMPLETE
            and item.temporal_scope == "point_in_time"
            and not item.limitations
            and (item.reported_records is None or item.reported_records >= item.returned_records)
        )
        intervals = DateIntervalSet(
            tuple(DateInterval(item.scanned_start, item.scanned_end) for item in watermarks)
        )
        has_gap = bool(
            intervals.intervals
            and intervals.gaps(intervals.intervals[0].start, intervals.intervals[-1].end)
        )
        baseline_cutoff = revision.update_summary.baseline_cutoff
        has_required_overlap = revision.role is ResearchRevisionRole.INITIAL or (
            baseline_cutoff is not None
            and baseline_cutoff < revision.cutoff
            and any(
                item.baseline_cutoff == baseline_cutoff
                and item.overlap_start is not None
                and item.scanned_start <= item.overlap_start
                and item.overlap_start <= baseline_cutoff <= item.scanned_end
                for item in applicable
            )
        )
        if (
            not applicable
            or not has_required_overlap
            or has_gap
            or any(
                item.scanned_end > revision.cutoff
                or item.status is not CoverageStatus.COMPLETE
                or item.temporal_scope != "point_in_time"
                or item.limitations
                or (
                    item.reported_records is not None
                    and item.reported_records < item.returned_records
                )
                for item in watermarks
            )
        ):
            return False
        records = self.records_by_source.get(source, ())
        return all(
            watermark.returned_records == 0
            or any(record.version_id in self.lineage_version_ids for record in records)
            for watermark in applicable
        )
