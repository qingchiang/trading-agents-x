"""Opt-in, read-only bounded-update probes for reviewed Japanese chain heads.

Supply one to five JSON cases in ``TRADINGAGENTS_INCREMENTAL_LIVE_CASES``.
Each case has ``chain_id``, ``analysis_date``, and ``expected``. The probe
collects live data but never advances a Research Chain.
"""

from __future__ import annotations

import json
import os

import pytest

from tradingagents.application.contracts import AnalysisRequest
from tradingagents.application.incremental import run_deterministic_incremental_gate
from tradingagents.application.repository import RunRepository
from tradingagents.application.settings import AppSettings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_data,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_DATA_TESTS") != "1",
        reason="Set RUN_LIVE_DATA_TESTS=1 to run live market-data contracts",
    ),
]


def test_reviewed_japanese_incremental_cases_are_sanitized_and_fail_closed(
    live_endpoint,
) -> None:
    raw_cases = os.environ.get("TRADINGAGENTS_INCREMENTAL_LIVE_CASES")
    if not raw_cases:
        pytest.skip("Set TRADINGAGENTS_INCREMENTAL_LIVE_CASES to reviewed JSON cases")
    cases = json.loads(raw_cases)
    assert isinstance(cases, list) and 1 <= len(cases) <= 5
    settings = AppSettings.from_env(environ=os.environ, load_env_files=False)
    assert settings.research_update_mode == "shadow"
    repository = RunRepository(settings)

    for case in cases:
        assert set(case) == {"chain_id", "analysis_date", "expected"}
        chain = repository.get_research_chain(str(case["chain_id"]))
        assert chain.instrument in settings.experimental_nmc_jp_whitelist
        request = AnalysisRequest(
            ticker=chain.instrument,
            analysis_date=str(case["analysis_date"]),
        )
        run_settings = settings.resolve_run(request)
        with live_endpoint(
            f"incremental:{chain.instrument}",
            source="bounded Japanese collection",
        ) as probe:
            result = run_deterministic_incremental_gate(
                chain.current_revision,
                request,
                run_settings.dataflow_config(settings),
                lambda: False,
            )
            observed = (
                result.candidate.outcome.value
                if result.candidate is not None
                else result.escalation_reason.value
            )
            probe.observe(
                source="bounded Japanese collection",
                last_observation=(
                    f"ticker={chain.instrument};cutoff={request.analysis_date};"
                    f"result={observed};tool_calls={result.metrics.tool_calls}"
                ),
            )
        assert observed == case["expected"]
