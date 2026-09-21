"""Structured source results preserve audit data independently of prose."""

from datetime import UTC, date, datetime

from tradingagents.domain.data import ProvenanceRecord, SourceObservation
from tradingagents.domain.data_result import DataDiagnostic, DataResult


def test_composed_result_keeps_source_boundaries_and_diagnostics():
    official = ProvenanceRecord("income", "official", timing="disclosure-date filtered")
    live = ProvenanceRecord("income", "supplement", timing="live non-point-in-time")
    observed = SourceObservation(
        "official", "financial_income", "2025", {"income": 12},
        datetime(2026, 1, 2, tzinfo=UTC), available_on=date(2026, 1, 2),
    )
    first = DataResult("official values", observations=(observed,), provenance=(official,))
    second = DataResult("live detail", provenance=(live,), diagnostics=(DataDiagnostic("bounded_detail", "supplement"),))
    combined = DataResult.combine((first.with_scope("point_in_time"), second.with_scope("live_only")))
    assert combined.content == "official values\n\nlive detail"
    assert combined.observations == (observed,)
    assert [span.temporal_scope for span in combined.spans] == ["point_in_time", "live_only"]
    assert combined.spans[0].records == (official,)
    assert combined.spans[1].records == (live,)
    assert combined.diagnostics[0].code == "bounded_detail"
    assert DataResult.load(combined.dump()) == combined
