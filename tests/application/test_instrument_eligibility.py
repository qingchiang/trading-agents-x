from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import text
from yfinance.exceptions import YFRateLimitError

from tradingagents.application.contracts import AnalysisRequest, RunStatus
from tradingagents.application.database import RunRecord
from tradingagents.application.errors import (
    InstrumentEligibilityUnavailableError,
    UnsupportedInstrumentError,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.client import TradingAgents
from tradingagents.dataflows import instrument_identity as identity_dataflow
from tradingagents.dataflows.errors import VendorError, VendorRateLimitError
from tradingagents.dataflows.instrument_identity import resolve_instrument_eligibility


def _request(ticker: str = "NVDA") -> AnalysisRequest:
    return AnalysisRequest(ticker=ticker, analysis_date=date(2026, 7, 24))


def _with_eligibility_vendor(
    app_settings: AppSettings,
    vendor: str,
) -> AppSettings:
    data_config = deepcopy(dict(app_settings.default_run_settings.data_config))
    data_config["data_vendors"]["instrument_eligibility"] = vendor
    return app_settings.model_copy(
        update={
            "default_run_settings": app_settings.default_run_settings.model_copy(
                update={"data_config": data_config}
            )
        }
    )


@pytest.mark.parametrize(
    ("result", "error"),
    [
        ({"symbol": "SPY", "quote_type": "ETF"}, UnsupportedInstrumentError),
        ({"symbol": "VTI", "quote_type": "MUTUALFUND"}, UnsupportedInstrumentError),
        ({"symbol": "NVDA"}, InstrumentEligibilityUnavailableError),
        ({"symbol": "MSFT", "quote_type": "EQUITY"},
         InstrumentEligibilityUnavailableError),
        ({"symbol": "NVDA", "quote_type": "EQUITY", "fuzzy": True},
         InstrumentEligibilityUnavailableError),
        ([{"symbol": "NVDA", "quote_type": "EQUITY"},
          {"symbol": "NVDA", "quote_type": "EQUITY"}],
         InstrumentEligibilityUnavailableError),
        ([{"symbol": "NVDA", "quote_type": "EQUITY"}, "malformed"],
         InstrumentEligibilityUnavailableError),
        ({"symbol": "NVDA", "quote_type": "EQUITY", "security_type": 7},
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
        ticker = (
            result["symbol"]
            if isinstance(result, dict) and result["symbol"] in {"SPY", "VTI"}
            else "NVDA"
        )
        service.enqueue(_request(ticker))

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


def test_default_resolver_honors_configured_eligibility_vendor(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    settings = _with_eligibility_vendor(app_settings, "alpha_vantage")
    yahoo_called = False

    def search(**_kwargs):
        nonlocal yahoo_called
        yahoo_called = True
        return type(
            "SearchResult",
            (),
            {"quotes": [{"symbol": "NVDA", "quoteType": "EQUITY"}]},
        )()

    monkeypatch.setattr(identity_dataflow.yf, "Search", search)
    service = AnalysisService(settings, repository=repository)

    with pytest.raises(InstrumentEligibilityUnavailableError):
        service.enqueue(_request())

    assert yahoo_called is False
    assert repository.list_runs().total == 0


def test_public_python_default_honors_configured_eligibility_vendor(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    settings = _with_eligibility_vendor(app_settings, "alpha_vantage")
    yahoo_called = False

    def search(**_kwargs):
        nonlocal yahoo_called
        yahoo_called = True
        return type(
            "SearchResult",
            (),
            {"quotes": [{"symbol": "NVDA", "quoteType": "EQUITY"}]},
        )()

    monkeypatch.setattr(identity_dataflow.yf, "Search", search)
    application = TradingAgents(settings)

    with pytest.raises(InstrumentEligibilityUnavailableError):
        application.enqueue(_request())

    assert yahoo_called is False
    assert repository.list_runs().total == 0


def test_yfinance_eligibility_wraps_provider_failures(
    monkeypatch,
) -> None:
    def fail(_operation):
        raise RuntimeError("provider transport failed")

    monkeypatch.setattr(identity_dataflow, "yf_retry", fail)

    with pytest.raises(VendorError, match="eligibility lookup failed"):
        resolve_instrument_eligibility("NVDA")


def test_yfinance_eligibility_preserves_rate_limit_semantics(
    monkeypatch,
) -> None:
    def rate_limited(_operation):
        raise YFRateLimitError

    monkeypatch.setattr(identity_dataflow, "yf_retry", rate_limited)

    with pytest.raises(VendorRateLimitError, match="rate limited"):
        resolve_instrument_eligibility("NVDA")


def test_provider_non_string_classification_cannot_be_reduced_to_equity(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    search = type(
        "SearchResult",
        (),
        {"quotes": [{"symbol": "NVDA", "quoteType": "EQUITY", "securityType": 7}]},
    )()
    monkeypatch.setattr(identity_dataflow.yf, "Search", lambda **_kwargs: search)
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=resolve_instrument_eligibility,
    )

    with pytest.raises(InstrumentEligibilityUnavailableError):
        service.enqueue(_request())

    assert repository.list_runs().total == 0


@pytest.mark.parametrize("ticker", ["NVDA", "7203.T", "600519.SS", "000651.SZ"])
def test_representative_listed_equity_matrix_is_admitted(
    app_settings,
    repository,
    ticker,
) -> None:
    observed: list[str] = []

    def resolve(symbol: str):
        observed.append(symbol)
        return {"symbol": symbol, "quote_type": "EQUITY"}

    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=resolve,
    )

    queued = service.enqueue(_request(ticker))

    assert queued.request.ticker == ticker
    assert observed == [ticker]
    assert repository.list_runs().total == 1


def test_missing_resolver_cannot_construct_a_permissive_service(
    app_settings,
    repository,
) -> None:
    with pytest.raises(TypeError, match="eligibility_resolver is required"):
        AnalysisService(
            app_settings,
            repository=repository,
            eligibility_resolver=None,
        )


def test_retry_revalidates_legacy_non_equity_before_requeue(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=lambda ticker: {
            "symbol": ticker,
            "quote_type": "ETF" if ticker == "SPY" else "EQUITY",
        },
    )
    queued = service.enqueue(_request())
    repository.claim_run(queued.id, "fixture-worker", 30)
    repository.fail(queued.id, RuntimeError("fixture failure"))
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, queued.id)
        record.request_json = {**record.request_json, "ticker": "SPY"}

    with pytest.raises(UnsupportedInstrumentError):
        service.retry(queued.id)

    assert repository.get_run(queued.id).status is RunStatus.FAILED
    assert repository.get_run(queued.id).attempt == 1


def test_source_run_revalidates_legacy_non_equity_before_creation(
    app_settings,
    repository,
) -> None:
    observed: list[str] = []

    def resolve(ticker: str):
        observed.append(ticker)
        return {
            "symbol": ticker,
            "quote_type": "ETF" if ticker == "SPY" else "EQUITY",
        }

    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=resolve,
    )
    source = service.enqueue(_request())
    repository.claim_run(source.id, "fixture-worker", 30)
    repository.fail(source.id, RuntimeError("fixture failure"))
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, source.id)
        record.request_json = {**record.request_json, "ticker": "SPY"}

    with pytest.raises(UnsupportedInstrumentError):
        service.enqueue(_request("AAPL"), source_run_id=source.id)

    assert observed == ["NVDA", "AAPL", "SPY"]
    assert repository.list_runs().total == 1


@pytest.mark.parametrize("operation", ["enqueue", "run"])
@pytest.mark.parametrize(
    ("ticker", "error"),
    [
        ("SPY", UnsupportedInstrumentError),
        ("NVDA", InstrumentEligibilityUnavailableError),
    ],
)
def test_public_python_operations_expose_typed_admission_errors(
    app_settings,
    repository,
    operation,
    ticker,
    error,
) -> None:
    def resolve(symbol: str):
        if symbol == "SPY":
            return {"symbol": symbol, "quote_type": "ETF"}
        return {"symbol": symbol, "quote_type": 17}

    application = TradingAgents(
        app_settings,
        eligibility_resolver=resolve,
    )

    with pytest.raises(error):
        getattr(application, operation)(_request(ticker))

    assert repository.list_runs().total == 0
