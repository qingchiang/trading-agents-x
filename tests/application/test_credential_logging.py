"""Credential-scoped diagnostics remain safe after exceptions leave the scope."""

import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

from tradingagents.application.worker import AnalysisWorker
from tradingagents.credentials import use_credentials


def test_worker_logs_redacted_failure_after_context_exit(caplog, app_settings):
    first = "fake-quick-secret+/="
    second = "fake-deep-secret"
    source = "fake-source-secret"
    service = Mock()
    service.configuration.read.return_value = SimpleNamespace(initialized=True)
    service.repository.claim_next.return_value = SimpleNamespace(id="fake-run")

    def fail(*args, **kwargs):
        with use_credentials(
            {"connection:q:api_key": first, "connection:d:api_key": second, "FRED_API_KEY": source}
        ):
            try:
                raise ValueError(
                    f"query failed https://example.invalid/?api_key={quote(source)} {second}"
                )
            except ValueError as cause:
                raise RuntimeError(f"model failed {quote(first, safe='')}") from cause

    service.execute_claimed.side_effect = fail
    worker = AnalysisWorker(app_settings, service=service, maintenance=Mock())
    assert worker.run_once()
    assert "RuntimeError" in caplog.text
    assert "ValueError" in caplog.text
    assert "[REDACTED]" in caplog.text
    for key in (first, second, source, quote(first, safe="")):
        assert key not in caplog.text


def test_concurrent_scoped_warning_and_exception_logs_do_not_expose_keys(caplog):
    logger = logging.getLogger("fixture.provider")
    keys = ["first-scope-key", "second-scope-key"]

    def emit(key):
        with use_credentials({"key": key}):
            logger.warning("request failed: %s", key)
            try:
                raise RuntimeError(key)
            except RuntimeError:
                logger.exception("request exception %s", key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(emit, keys))
    assert all(key not in caplog.text for key in keys)
    assert caplog.text.count("[REDACTED]") >= 4
    assert "RuntimeError" in caplog.text


def test_unscoped_worker_failure_omits_untrusted_exception_details(caplog, app_settings):
    service = Mock()
    service.configuration.read.return_value = SimpleNamespace(initialized=True)
    service.repository.claim_next.return_value = SimpleNamespace(id="fake-run")
    service.execute_claimed.side_effect = RuntimeError("unsafe-unscoped-detail")
    assert AnalysisWorker(app_settings, service=service, maintenance=Mock()).run_once()
    assert "RuntimeError" in caplog.text
    assert "unsafe-unscoped-detail" not in caplog.text
