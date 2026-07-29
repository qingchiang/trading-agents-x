#!/usr/bin/env python3
"""Run the exact main-branch Analyst or Medium prompts on frozen inputs.

This adapter is deliberately outside the production package. It imports
TradingAgentsX from an explicit independent main-branch worktree and writes the
same neutral JSON record shape consumed by the current quality evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

_LIVE_GATE = "RUN_LIVE_LLM_EVALS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--mode", choices=("analyst", "medium"), required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--quick-model", required=True)
    parser.add_argument("--deep-model", required=True)
    parser.add_argument("--quick-reasoning")
    parser.add_argument("--deep-reasoning")
    parser.add_argument("--output-language", default="en")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or os.environ.get(_LIVE_GATE) != "1":
        raise RuntimeError(
            "Review the call plan and obtain explicit authorization before "
            f"passing --execute with {_LIVE_GATE}=1."
        )
    args.worktree = _activate_worktree(args.worktree)
    args.worktree_commit = _verify_worktree_commit(
        args.worktree,
        args.expected_commit,
    )
    _verify_import_origin(args.worktree)
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    selected = set(args.cases or ())
    cases = [
        case
        for case in suite["cases"]
        if not selected or case["case_id"] in selected
    ]
    missing = selected - {case["case_id"] for case in cases}
    if missing:
        raise ValueError("unknown case IDs: " + ", ".join(sorted(missing)))

    jobs = (
        [
            (case, analyst_input)
            for case in cases
            for analyst_input in case["analyst_inputs"]
        ]
        if args.mode == "analyst"
        else [(case, None) for case in cases]
    )
    records = []
    for case, analyst_input in jobs:
        if analyst_input is None and not case.get("reports"):
            raise ValueError(f"{case['case_id']} has no approved AnalystReports")
        for repetition in range(1, args.repetitions + 1):
            tracker = _Tracker()
            quick, deep = _llms(args, tracker)
            if analyst_input is not None:
                prompt_hash = _baseline_prompt_contract_hash(
                    worktree=args.worktree,
                    mode="analyst",
                    case=case,
                    analyst_input=analyst_input,
                )
                output = _run_analyst(case, analyst_input, quick)
                variant = "main_analyst"
                record_case_id = (
                    f"{case['case_id']}:{analyst_input['analyst']}"
                )
                evidence = analyst_input["evidence"]
                reports = {}
                risk_recall = 1.0
                issues = (
                    []
                    if output.strip()
                    else [
                        {
                            "severity": "severe",
                            "code": "baseline.output_missing",
                            "location": "raw_baseline_output.report",
                            "message": "The main Analyst produced no final report.",
                        }
                    ]
                )
            else:
                prompt_hash = _baseline_prompt_contract_hash(
                    worktree=args.worktree,
                    mode="medium",
                    case=case,
                )
                output = _run_medium(case, quick, deep)
                variant = "main_medium"
                record_case_id = case["case_id"]
                evidence = case["evidence"]
                reports = case["reports"]
                risk_recall = _risk_recall(
                    output["final_trade_decision"],
                    case.get("expected_risk_terms") or [],
                )
                issues = []
            record = _record(
                args=args,
                case=case,
                record_case_id=record_case_id,
                evidence=evidence,
                repetition=repetition,
                variant=variant,
                reports=reports,
                output=output,
                tracker=tracker,
                prompt_hash=prompt_hash,
                risk_recall=risk_recall,
                issues=issues,
            )
            records.append(record)
            _persist(args.output_dir, records, record)
            print(
                json.dumps(
                    {
                        "record_id": (
                            f"{variant}:{record_case_id}:{repetition}"
                        ),
                        "llm_calls": tracker.llm_calls,
                        "input_tokens": tracker.input_tokens,
                        "output_tokens": tracker.output_tokens,
                        "wall_time_seconds": round(tracker.elapsed, 3),
                    }
                ),
                flush=True,
            )
    return 0


def _activate_worktree(worktree: Path) -> Path:
    root = worktree.expanduser().resolve()
    if not (root / "tradingagents").is_dir():
        raise ValueError("worktree does not contain the tradingagents package")
    script_root = Path(__file__).resolve().parents[2]
    sys.path[:] = [
        str(root),
        *(
            entry
            for entry in sys.path
            if Path(entry or ".").resolve() != script_root
        ),
    ]
    return root


def _verify_worktree_commit(worktree: Path, expected: str) -> str:
    expected = expected.strip().lower()
    if len(expected) != 40 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise ValueError("--expected-commit must be a full 40-character SHA")
    actual = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual != expected:
        raise ValueError(
            f"baseline worktree is {actual}, expected {expected}"
        )
    return actual


def _verify_import_origin(worktree: Path) -> None:
    spec = importlib.util.find_spec("tradingagents")
    if spec is None or spec.origin is None:
        raise RuntimeError("baseline tradingagents package cannot be resolved")
    origin = Path(spec.origin).resolve()
    if worktree not in origin.parents:
        raise RuntimeError(
            f"baseline package resolved from {origin}, not {worktree}"
        )


class _NoToolLLM:
    def __init__(self, llm: Any):
        self._llm = llm

    def bind_tools(self, tools, **_kwargs):
        return self._llm.bind_tools(tools, tool_choice="none")


def _llms(args: argparse.Namespace, tracker: _Tracker) -> tuple[Any, Any]:
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.llm_clients import create_llm_client
    from tradingagents.llm_clients.reasoning_effort import (
        RESOLVED_MARKER,
        resolve_reasoning_effort,
    )

    config = {
        **DEFAULT_CONFIG,
        "llm_provider": args.provider,
        "quick_think_llm": args.quick_model,
        "deep_think_llm": args.deep_model,
        "quick_reasoning_effort": args.quick_reasoning,
        "deep_reasoning_effort": args.deep_reasoning,
        "temperature": args.temperature,
        "llm_max_retries": args.max_retries,
        "output_language": args.output_language,
        "provenance_appendix": False,
    }
    set_config(config)

    def build(role: str, model: str):
        kwargs: dict[str, Any] = {"callbacks": [tracker]}
        if args.temperature is not None:
            kwargs["temperature"] = args.temperature
        if args.max_retries is not None:
            kwargs["max_retries"] = args.max_retries
        effort = resolve_reasoning_effort(config, role)
        kwargs.update(effort.kwargs)
        if effort.kwargs:
            kwargs[RESOLVED_MARKER] = True
        return create_llm_client(
            provider=args.provider,
            model=model,
            base_url=config.get("backend_url"),
            **kwargs,
        ).get_llm()

    return build("quick", args.quick_model), build("deep", args.deep_model)


def _run_analyst(
    case: dict[str, Any],
    analyst_input: dict[str, Any],
    quick: Any,
) -> str:
    from langchain_core.messages import HumanMessage
    from tradingagents.graph.propagation import Propagator

    analyst = analyst_input["analyst"]
    state = Propagator().create_initial_state(
        case["evidence"]["instrument"],
        case["evidence"]["analysis_date"],
        asset_type=(
            "crypto"
            if case["evidence"]["instrument"].endswith("-USD")
            else "stock"
        ),
        instrument_context=(
            f"Frozen evaluation instrument: {case['evidence']['instrument']}."
        ),
    )
    state["messages"] = [
        HumanMessage(
            content=(
                "Use the following frozen tool response as the complete data "
                "available for this evaluation. Do not call another tool. "
                "Write the final report now.\n\n"
                + analyst_input["tool_response"]
            )
        )
    ]
    if analyst == "market":
        from tradingagents.agents import create_market_analyst

        node = create_market_analyst(_NoToolLLM(quick))
        key = "market_report"
    elif analyst == "fundamentals":
        from tradingagents.agents import create_fundamentals_analyst

        node = create_fundamentals_analyst(_NoToolLLM(quick))
        key = "fundamentals_report"
    elif analyst == "news":
        from tradingagents.agents.analysts import news_analyst

        news_analyst.get_global_macro_panel = (
            lambda _date: "<no separate macro panel in role-isolated fixture>"
        )
        news_analyst.is_tokyo_ticker = lambda _ticker: False
        node = news_analyst.create_news_analyst(_NoToolLLM(quick))
        key = "news_report"
    elif analyst == "social":
        from tradingagents.agents.analysts import sentiment_analyst
        from tradingagents.dataflows.market_signals import (
            FetchedSentimentSignal,
            SentimentSignal,
        )

        sources = sorted(
            {
                item["source"]
                for item in analyst_input["evidence"]["items"]
            }
        )
        source = " + ".join(sources)
        spec = SentimentSignal(
            tag="frozen_sentiment",
            fetch=lambda _ticker, _date: analyst_input["tool_response"],
            evidence="frozen role-local sentiment evidence",
            source=source,
            title=f"Frozen sentiment evidence — {source}",
            intro=(
                "Role-isolated, point-in-time evidence recorded for this "
                "evaluation. Treat unavailable coverage as uncertainty."
            ),
            effective=lambda date: f"available by {date}",
            timing="frozen point-in-time evaluation input",
        )
        signal = FetchedSentimentSignal(
            spec=spec,
            body=analyst_input["tool_response"],
        )
        sentiment_analyst.get_news = SimpleNamespace(
            func=lambda *_args, **_kwargs: (
                "<no separate ticker-news input in role-isolated fixture>"
            )
        )
        sentiment_analyst.market_suffix_of = lambda _ticker: ".EVAL"
        sentiment_analyst.fetch_sentiment_signals = (
            lambda _ticker, _date: (signal,)
        )
        sentiment_analyst.is_live = lambda _date: False
        state["messages"] = [
            HumanMessage(
                content=(
                    "Use only the pre-fetched frozen evidence in the system "
                    "prompt and produce the final sentiment report."
                )
            )
        ]
        node = sentiment_analyst.create_sentiment_analyst(quick)
        key = "sentiment_report"
    else:
        raise ValueError(f"unsupported Analyst role: {analyst}")
    result = node(state)
    return str(result.get(key, ""))


def _run_medium(
    case: dict[str, Any],
    quick: Any,
    deep: Any,
) -> dict[str, Any]:
    from tradingagents.graph.propagation import Propagator

    from tradingagents.agents import (
        create_aggressive_debator,
        create_bear_researcher,
        create_bull_researcher,
        create_conservative_debator,
        create_neutral_debator,
        create_portfolio_manager,
        create_research_manager,
        create_trader,
    )

    evidence = case["evidence"]
    state = Propagator().create_initial_state(
        evidence["instrument"],
        evidence["analysis_date"],
        asset_type=(
            "crypto" if evidence["instrument"].endswith("-USD") else "stock"
        ),
        instrument_context=f"Frozen evaluation instrument: {evidence['instrument']}.",
    )
    rendered = {
        key: _render_report(report)
        for key, report in case["reports"].items()
    }
    state.update(
        {
            "market_report": rendered.get("market", ""),
            "sentiment_report": rendered.get("social", ""),
            "news_report": rendered.get("news", ""),
            "fundamentals_report": rendered.get("fundamentals", ""),
        }
    )
    bull = create_bull_researcher(quick)
    bear = create_bear_researcher(quick)
    for _round in range(3):
        state.update(bull(state))
        state.update(bear(state))
    state.update(create_research_manager(deep)(state))
    state.update(create_trader(quick)(state))
    aggressive = create_aggressive_debator(quick)
    conservative = create_conservative_debator(quick)
    neutral = create_neutral_debator(quick)
    for _round in range(3):
        state.update(aggressive(state))
        state.update(conservative(state))
        state.update(neutral(state))
    state.update(create_portfolio_manager(deep)(state))
    return {
        "investment_debate": state["investment_debate_state"],
        "investment_plan": state["investment_plan"],
        "trader_plan": state["trader_investment_plan"],
        "risk_debate": state["risk_debate_state"],
        "final_trade_decision": state["final_trade_decision"],
    }


def _render_report(report: dict[str, Any]) -> str:
    parts = [f"# {report['analyst'].title()} Analyst", report["executive_summary"]]
    tables = {table["id"]: table for table in report.get("tables", [])}
    for section in report["sections"]:
        parts.extend([f"## {section['title']}", section["narrative"]])
        for table_id in section.get("table_ids", []):
            table = tables.get(table_id)
            if table is not None:
                parts.append(_render_table(table))
    if report.get("catalysts"):
        parts.extend(
            ["## Catalysts", *[f"- {item}" for item in report["catalysts"]]]
        )
    parts.extend(["## Risks", *[f"- {item}" for item in report["risks"]]])
    parts.extend(
        [
            "## Invalidation Conditions",
            *[
                f"- {item}"
                for item in report["invalidation_conditions"]
            ],
        ]
    )
    return "\n\n".join(parts)


def _render_table(table: dict[str, Any]) -> str:
    columns = table["columns"]
    lines = [
        f"### {table['title']}",
        table["purpose"],
        "| " + " | ".join(column["label"] for column in columns) + " |",
        "| " + " | ".join("---" for _column in columns) + " |",
    ]
    for row in table["rows"]:
        lines.append(
            "| "
            + " | ".join(
                str(row["cells"][column["key"]]["display_value"])
                for column in columns
            )
            + " |"
        )
    return "\n".join(lines)


class _Tracker(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._started = monotonic()
        self._run_ids: set[Any] = set()
        self._prompt_hashes: list[str] = []
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def elapsed(self) -> float:
        return max(0.0, monotonic() - self._started)

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self._capture({"prompts": prompts}, kwargs)

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        from langchain_core.messages import message_to_dict

        self._capture(
            {
                "messages": [
                    [message_to_dict(message) for message in batch]
                    for batch in messages
                ]
            },
            kwargs,
        )

    def _capture(self, payload, kwargs) -> None:
        run_id = kwargs.get("run_id")
        digest = _hash(payload)
        with self._lock:
            if run_id is not None and run_id in self._run_ids:
                return
            if run_id is not None:
                self._run_ids.add(run_id)
            self.llm_calls += 1
            self._prompt_hashes.append(digest)

    def on_llm_end(self, response, **kwargs) -> None:
        input_tokens, output_tokens = _token_usage(response)
        with self._lock:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens

    @property
    def prompt_hash(self) -> str:
        with self._lock:
            return _hash(sorted(self._prompt_hashes))


def _token_usage(response) -> tuple[int, int]:
    try:
        message = response.generations[0][0].message
    except (AttributeError, IndexError, TypeError):
        message = None
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0)), int(
            usage.get("output_tokens", 0)
        )
    raw = (getattr(response, "llm_output", None) or {}).get(
        "token_usage",
        {},
    )
    return int(raw.get("input_tokens", raw.get("prompt_tokens", 0))), int(
        raw.get("output_tokens", raw.get("completion_tokens", 0))
    )


def _risk_recall(text: str, risks: list[str]) -> float:
    if not risks:
        return 1.0
    normalized = text.casefold()
    return sum(risk.casefold() in normalized for risk in risks) / len(risks)


def _record(
    *,
    args: argparse.Namespace,
    case: dict[str, Any],
    record_case_id: str,
    evidence: dict[str, Any],
    repetition: int,
    variant: str,
    reports: dict[str, Any],
    output: Any,
    tracker: _Tracker,
    prompt_hash: str,
    risk_recall: float,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "suite_version": "2",
        "variant": variant,
        "case_id": record_case_id,
        "source_case_id": case["case_id"],
        "repetition": repetition,
        "runtime": {
            "commit_sha": args.worktree_commit,
            "provider": args.provider,
            "quick_model": args.quick_model,
            "deep_model": args.deep_model,
            "quick_reasoning_effort": args.quick_reasoning,
            "deep_reasoning_effort": args.deep_reasoning,
            "output_language": args.output_language,
            "temperature": args.temperature,
        },
        "prompt_hash": prompt_hash,
        "runtime_prompt_hash": tracker.prompt_hash,
        "evidence_hash": evidence["digest"],
        "output_hash": _hash(output),
        "metrics": {
            "llm_calls": tracker.llm_calls,
            "tool_calls": 0,
            "input_tokens": tracker.input_tokens,
            "output_tokens": tracker.output_tokens,
            "wall_time_seconds": tracker.elapsed,
            "node_metrics": {},
        },
        "risk_recall": risk_recall,
        "issues": issues,
        "evidence": evidence,
        "reports": reports,
        "decision": None,
        "artifacts": [],
        "raw_baseline_output": (
            {"report": output} if isinstance(output, str) else output
        ),
    }


def _persist(
    output_dir: Path,
    records: list[dict[str, Any]],
    record: dict[str, Any],
) -> None:
    path = (
        output_dir
        / "records"
        / record["variant"]
        / record["case_id"]
        / f"{record['repetition']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))
    _atomic_write(
        output_dir / "records.jsonl",
        "\n".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            for item in records
        )
        + "\n",
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _baseline_prompt_contract_hash(
    *,
    worktree: Path,
    mode: str,
    case: dict[str, Any],
    analyst_input: dict[str, Any] | None = None,
) -> str:
    if mode == "analyst":
        if analyst_input is None:
            raise ValueError("Analyst prompt hashing requires an Analyst input")
        module = {
            "market": "market_analyst.py",
            "social": "sentiment_analyst.py",
            "news": "news_analyst.py",
            "fundamentals": "fundamentals_analyst.py",
        }[analyst_input["analyst"]]
        paths = [
            f"tradingagents/agents/analysts/{module}",
            "tradingagents/agents/utils/agent_utils.py",
        ]
        if analyst_input["analyst"] == "social":
            paths.extend(
                (
                    "tradingagents/agents/schemas.py",
                    "tradingagents/agents/utils/structured.py",
                )
            )
        frozen_input: Any = analyst_input["tool_response"]
        evidence_hash = analyst_input["evidence"]["digest"]
    elif mode == "medium":
        paths = [
            "tradingagents/agents/researchers/bull_researcher.py",
            "tradingagents/agents/researchers/bear_researcher.py",
            "tradingagents/agents/managers/research_manager.py",
            "tradingagents/agents/trader/trader.py",
            "tradingagents/agents/risk_mgmt/aggressive_debator.py",
            "tradingagents/agents/risk_mgmt/neutral_debator.py",
            "tradingagents/agents/risk_mgmt/conservative_debator.py",
            "tradingagents/agents/managers/portfolio_manager.py",
            "tradingagents/agents/schemas.py",
            "tradingagents/agents/utils/structured.py",
        ]
        frozen_input = case["reports"]
        evidence_hash = case["evidence"]["digest"]
    else:
        raise ValueError(f"unsupported baseline mode: {mode}")
    source_hashes = {
        path: hashlib.sha256((worktree / path).read_bytes()).hexdigest()
        for path in paths
    }
    return _hash(
        {
            "contract": f"main-{mode}",
            "source_hashes": source_hashes,
            "instrument": case["evidence"]["instrument"],
            "analysis_date": case["evidence"]["analysis_date"],
            "evidence_hash": evidence_hash,
            "frozen_input": frozen_input,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
