"""Structured source results preserve audit data independently of prose."""

from datetime import UTC, date, datetime

from tradingagents.domain.data import ProvenanceRecord, SourceObservation
from tradingagents.domain.data_result import DataDiagnostic, DataResult


def test_composed_result_keeps_source_boundaries_and_diagnostics():
    official = ProvenanceRecord("income", "official", timing="disclosure-date filtered")
    live = ProvenanceRecord("income", "supplement", timing="live non-point-in-time")
    observed = SourceObservation(
        "official",
        "financial_income",
        "2025",
        {"income": 12},
        datetime(2026, 1, 2, tzinfo=UTC),
        available_on=date(2026, 1, 2),
    )
    first = DataResult("official values", observations=(observed,), provenance=(official,))
    second = DataResult(
        "live detail",
        provenance=(live,),
        diagnostics=(DataDiagnostic("bounded_detail", "supplement"),),
    )
    combined = DataResult.combine(
        (first.with_scope("point_in_time"), second.with_scope("live_only"))
    )
    assert combined.content == "official values\n\nlive detail"
    assert combined.observations == (observed,)
    assert [span.temporal_scope for span in combined.spans] == ["point_in_time", "live_only"]
    assert combined.spans[0].records == (official,)
    assert combined.spans[1].records == (live,)
    assert combined.diagnostics[0].code == "bounded_detail"
    assert DataResult.load(combined.dump()) == combined


def test_news_cache_preserves_producer_identity_independently_of_rendering(tmp_path):
    from tradingagents.data.news_cache import fetch_news_feed
    from tradingagents.domain.news import NewsCandidate

    row = NewsCandidate(
        "official",
        "Producer title",
        "### Presentation heading\nPlain display only.",
        "2026-09-03T10:00:00+09:00",
        record_id="official-1",
    )
    feed = DataResult("Rendered feed", news=(row,), news_header="## Official feed")
    calls = []

    def fetch():
        calls.append(1)
        return feed

    kwargs = {
        "config": {"data_cache_dir": str(tmp_path)},
        "now": lambda: datetime(2026, 9, 4, tzinfo=UTC),
    }
    first = fetch_news_feed("official", "9984.T", "2026-09-01", "2026-09-04", fetch, **kwargs)
    second = fetch_news_feed("official", "9984.T", "2026-09-01", "2026-09-04", fetch, **kwargs)
    assert calls == [1]
    assert first.news == second.news
    assert first.news[0].title == "Producer title"
    assert first.news[0].record_id == "official-1"
    assert first.news[0].retrieved_at == "2026-09-04T00:00:00+00:00"
