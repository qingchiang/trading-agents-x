import pytest

from tests.dataflows.test_incremental_us_collector import _request
from tradingagents.application.contracts import CollectionDiagnostic, CollectionDomainResult
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
    VendorTransportError,
)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (VendorRateLimitError("secret rate response"), "rate_limited"),
        (VendorTransportError("secret transport response"), "transport_failure"),
        (VendorNotConfiguredError("secret configuration"), "not_configured"),
        (NoMarketDataError("secret-symbol", detail="secret no rows"), "no_usable_data"),
    ],
)
def test_news_context_keeps_typed_diagnostic_and_continues_macro(
    monkeypatch, failure, expected_code
):
    from tradingagents.dataflows import incremental_inputs

    calls = []

    def route(*_args, **_kwargs):
        calls.append("global_news")
        raise failure

    def macro(*_args):
        calls.append("macro")
        return ""

    monkeypatch.setattr(incremental_inputs, "get_global_macro_panel", macro)
    domain = CollectionDomainResult(
        domain="news",
        state="unavailable",
        diagnostic=CollectionDiagnostic(code="news_retrieval_failed"),
    )

    result, candidates = incremental_inputs.append_news_context(
        _request(enabled_domains=("news",)), domain, route
    )

    assert calls == ["global_news", "macro"]
    assert candidates == ()
    assert result.sources == ()
    assert result.diagnostic.code == (
        f"news_retrieval_failed.news_context_partial.{expected_code}"
    )
    assert "secret" not in str(result)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (VendorRateLimitError("secret rate response"), "rate_limited"),
        (VendorTransportError("secret transport response"), "transport_failure"),
        (VendorNotConfiguredError("secret configuration"), "not_configured"),
        (NoMarketDataError("secret-symbol", detail="secret no rows"), "no_usable_data"),
    ],
)
def test_market_context_keeps_typed_snapshot_diagnostic_without_fake_source(
    failure, expected_code
):
    from tradingagents.dataflows import incremental_inputs

    def route(*_args, **_kwargs):
        raise failure

    domain = CollectionDomainResult(
        domain="market",
        state="unavailable",
        diagnostic=CollectionDiagnostic(code="market_route_failure"),
    )

    result, candidates = incremental_inputs.append_market_context(
        _request(enabled_domains=("market",)), domain, None, route
    )

    assert candidates == ()
    assert result.sources == ()
    assert result.diagnostic.code == (
        f"market_route_failure.market_snapshot_unavailable.{expected_code}"
    )
    assert "secret" not in str(result)
