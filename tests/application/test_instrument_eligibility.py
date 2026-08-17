from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from tradingagents.application.contracts import AnalysisRequest, RunStatus
from tradingagents.application.errors import (
    InstrumentEligibilityUnavailableError,
    UnsupportedInstrumentError,
)
from tradingagents.application.service import AnalysisService


def _request(ticker: str = "NVDA") -> AnalysisRequest:
    return AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 24))


@pytest.mark.parametrize(
    ("result", "error"),
    [
        ({"symbol": "SPY", "quote_type": "ETF"}, UnsupportedInstrumentError),
        ({"symbol": "NVDA"}, InstrumentEligibilityUnavailableError),
        ({"symbol": "MSFT", "quote_type": "EQUITY"},
         InstrumentEligibilityUnavailableError),
        ({"symbol": "NVDA", "quote_type": "EQUITY", "fuzzy": True},
         InstrumentEligibilityUnavailableError),
        ([{"symbol": "NVDA", "quote_type": "EQUITY"},
          {"symbol": "NVDA", "quote_type": "EQUITY"}],
         InstrumentEligibilityUnavailableError),
    ],
)
def test_admission_rejects_non_affirmative_eligibility_before_persistence(
    app_settings,
    repository,
    result,
    error,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=lambda _ticker: result,
    )

    with pytest.raises(error):
        service.enqueue(_request("SPY" if error is UnsupportedInstrumentError else "NVDA"))

    with repository.sessions() as session:
        assert session.execute(text("SELECT COUNT(*) FROM runs")).scalar_one() == 0


def test_execution_revalidates_before_graph_construction(app_settings, repository) -> None:
    responses = [{"symbol": "NVDA", "quote_type": "EQUITY"},
                 {"symbol": "NVDA", "quote_type": "ETF"}]

    def resolve(_ticker):
        return responses.pop(0)

    class Graph:
        def __init__(self, **_kwargs):
            pytest.fail("graph must not be constructed after revalidation failure")

    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=resolve,
        graph_factory=Graph,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
    )
    queued = service.enqueue(_request())
    claimed = repository.claim_run(queued.id, "fixture-worker", 30)

    with pytest.raises(UnsupportedInstrumentError):
        service.execute_claimed(claimed, worker_id="fixture-worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert repository.list_events(queued.id)[-1].event_type == "run.failed"


def test_resolver_failure_is_typed_as_temporarily_unavailable(
    app_settings,
    repository,
) -> None:
    def resolve(_ticker):
        raise ValueError("provider schema changed")

    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=resolve,
    )

    with pytest.raises(InstrumentEligibilityUnavailableError):
        service.enqueue(_request())
