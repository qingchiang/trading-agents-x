"""Test checkpoint resume: crash mid-analysis, re-run resumes from last node."""

import tempfile
import unittest
from typing import TypedDict
from unittest.mock import MagicMock

from langgraph.graph import END, StateGraph

from tradingagents.graph.checkpointer import (
    checkpoint_step,
    clear_checkpoint,
    get_checkpointer,
    has_checkpoint,
    thread_id,
)

# Mutable flag to simulate crash on first run
_should_crash = False


class _SimpleState(TypedDict):
    count: int


def _node_a(state: _SimpleState) -> dict:
    return {"count": state["count"] + 1}


def _node_b(state: _SimpleState) -> dict:
    if _should_crash:
        raise RuntimeError("simulated mid-analysis crash")
    return {"count": state["count"] + 10}


def _build_graph() -> StateGraph:
    builder = StateGraph(_SimpleState)
    builder.add_node("analyst", _node_a)
    builder.add_node("trader", _node_b)
    builder.set_entry_point("analyst")
    builder.add_edge("analyst", "trader")
    builder.add_edge("trader", END)
    return builder


class _LifecycleState(TypedDict):
    count: int
    visits: list[str]
    final_trade_decision: str


def _make_lifecycle_graph(tmpdir, should_crash, calls):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    def analyst(state: _LifecycleState) -> dict:
        calls["analyst"] += 1
        return {
            "count": state["count"] + 1,
            "visits": [*state["visits"], "analyst"],
        }

    def trader(state: _LifecycleState) -> dict:
        calls["trader"] += 1
        if should_crash["value"]:
            raise RuntimeError("simulated lifecycle crash")
        return {
            "count": state["count"] + 10,
            "visits": [*state["visits"], "trader"],
        }

    workflow = StateGraph(_LifecycleState)
    workflow.add_node("analyst", analyst)
    workflow.add_node("trader", trader)
    workflow.set_entry_point("analyst")
    workflow.add_edge("analyst", "trader")
    workflow.add_edge("trader", END)

    graph = object.__new__(TradingAgentsGraph)
    graph.config = {
        "checkpoint_enabled": True,
        "data_cache_dir": tmpdir,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
    }
    graph.selected_analysts = ("market",)
    graph.workflow = workflow
    graph.graph = workflow.compile()
    graph._checkpointer_ctx = None
    graph.debug = False
    graph.callbacks = []
    graph.ticker = None
    graph.curr_state = None
    graph.memory_log = MagicMock()
    graph.memory_log.get_past_context.return_value = "PAST CONTEXT"
    graph.resolve_instrument_context = MagicMock(return_value="IDENTITY")
    graph._resolve_pending_entries = MagicMock()
    graph._log_state = MagicMock()
    graph.process_signal = MagicMock(return_value="Hold")
    graph.propagator = MagicMock()
    graph.propagator.create_initial_state.return_value = {
        "count": 0,
        "visits": [],
        "final_trade_decision": "Rating: Hold",
    }
    graph.propagator.get_graph_args.return_value = {
        "stream_mode": "values",
        "config": {},
    }
    return graph


class TestCheckpointResume(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ticker = "TEST"
        self.date = "2026-04-20"

    def test_crash_and_resume(self):
        """Crash at 'trader' node, then resume from checkpoint."""
        global _should_crash
        builder = _build_graph()
        tid = thread_id(self.ticker, self.date)
        cfg = {"configurable": {"thread_id": tid}}

        # Run 1: crash at trader node
        _should_crash = True
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config=cfg)

        # Checkpoint should exist at step 1 (analyst completed)
        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))
        step = checkpoint_step(self.tmpdir, self.ticker, self.date)
        self.assertEqual(step, 1)

        # Run 2: resume — trader succeeds this time
        _should_crash = False
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke(None, config=cfg)

        # analyst added 1, trader added 10 → 11
        self.assertEqual(result["count"], 11)

    def test_clear_checkpoint_allows_fresh_start(self):
        """After clearing, the graph starts from scratch."""
        global _should_crash
        builder = _build_graph()
        tid = thread_id(self.ticker, self.date)
        cfg = {"configurable": {"thread_id": tid}}

        # Create a checkpoint by crashing
        _should_crash = True
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config=cfg)

        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))

        # Clear it
        clear_checkpoint(self.tmpdir, self.ticker, self.date)
        self.assertFalse(has_checkpoint(self.tmpdir, self.ticker, self.date))

        # Fresh run succeeds from scratch
        _should_crash = False
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke({"count": 0}, config=cfg)

        self.assertEqual(result["count"], 11)


    def test_different_date_starts_fresh(self):
        """A different date must NOT resume from an existing checkpoint."""
        global _should_crash
        builder = _build_graph()
        date2 = "2026-04-21"

        # Run with date1 — crash to leave a checkpoint
        _should_crash = True
        tid1 = thread_id(self.ticker, self.date)
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid1}})

        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))

        # date2 should have no checkpoint
        self.assertFalse(has_checkpoint(self.tmpdir, self.ticker, date2))

        # Run with date2 — should start fresh and succeed
        _should_crash = False
        tid2 = thread_id(self.ticker, date2)
        self.assertNotEqual(tid1, tid2)

        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid2}})

        # Fresh run: analyst +1, trader +10 = 11
        self.assertEqual(result["count"], 11)

        # Original date checkpoint still exists (untouched)
        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))


class TestCheckpointSignature(unittest.TestCase):
    """A different graph shape (analyst selection / depth / asset mode) must not
    resume the previous run's checkpoint (#1089)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ticker = "TEST"
        self.date = "2026-04-20"

    def test_empty_signature_is_legacy_id(self):
        self.assertEqual(
            thread_id(self.ticker, self.date),
            thread_id(self.ticker, self.date, ""),
        )

    def test_signature_changes_thread_id(self):
        legacy = thread_id(self.ticker, self.date)
        sig_a = thread_id(self.ticker, self.date, "analysts=market,news|asset=stock")
        sig_b = thread_id(self.ticker, self.date, "analysts=market|asset=stock")
        self.assertNotEqual(sig_a, sig_b)          # different graph shapes differ
        self.assertNotEqual(legacy, sig_a)         # signature-keyed differs from legacy
        self.assertEqual(                          # same inputs are stable
            sig_a, thread_id(self.ticker, self.date, "analysts=market,news|asset=stock")
        )

    def test_different_signature_starts_fresh(self):
        global _should_crash
        builder = _build_graph()
        sig1 = "analysts=market,news,fundamentals|asset=stock"
        sig2 = "analysts=market|asset=stock"       # dropped analysts -> different graph

        _should_crash = True
        tid1 = thread_id(self.ticker, self.date, sig1)
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid1}})

        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date, sig1))
        # A different graph shape has no checkpoint to resume from.
        self.assertFalse(has_checkpoint(self.tmpdir, self.ticker, self.date, sig2))

        _should_crash = False
        tid2 = thread_id(self.ticker, self.date, sig2)
        self.assertNotEqual(tid1, tid2)
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid2}})
        self.assertEqual(result["count"], 11)
        # sig1's checkpoint remains untouched.
        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date, sig1))

    def test_run_signature_captures_graph_shape(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        # Build a bare instance to exercise the pure helper without heavy __init__.
        g = object.__new__(TradingAgentsGraph)
        g.selected_analysts = ("market", "news")
        g.config = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 1}
        base = g._run_signature("stock")

        self.assertNotEqual(base, g._run_signature("crypto"))     # asset mode
        g.selected_analysts = ("market",)
        self.assertNotEqual(base, g._run_signature("stock"))      # analyst selection
        g.selected_analysts = ("market", "news")
        g.config = {"max_debate_rounds": 3, "max_risk_discuss_rounds": 1}
        self.assertNotEqual(base, g._run_signature("stock"))      # debate depth
        g.config = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 5}
        self.assertNotEqual(base, g._run_signature("stock"))      # risk depth
        # Stable for identical inputs.
        g.config = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 1}
        self.assertEqual(base, g._run_signature("stock"))


class TestTradingGraphCheckpointLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ticker = "TEST"
        self.date = "2026-04-20"
        self.should_crash = {"value": True}
        self.calls = {"analyst": 0, "trader": 0}
        self.graph = _make_lifecycle_graph(
            self.tmpdir,
            self.should_crash,
            self.calls,
        )

    def test_propagate_resumes_without_replaying_completed_nodes(self):
        with self.assertRaisesRegex(RuntimeError, "simulated lifecycle crash"):
            self.graph.propagate(self.ticker, self.date)

        signature = self.graph._run_signature("stock")
        self.assertTrue(
            has_checkpoint(self.tmpdir, self.ticker, self.date, signature)
        )
        self.assertEqual(self.calls, {"analyst": 1, "trader": 1})
        self.graph.memory_log.store_decision.assert_not_called()

        self.should_crash["value"] = False
        events = []
        final_state, decision = self.graph.propagate(
            self.ticker,
            self.date,
            on_chunk=lambda state, step: events.append((state, step)),
        )

        self.assertEqual(final_state["count"], 11)
        self.assertEqual(final_state["visits"], ["analyst", "trader"])
        self.assertEqual(decision, "Hold")
        self.assertEqual(self.calls, {"analyst": 1, "trader": 2})
        self.assertIsNotNone(events[0][1])
        self.assertTrue(all(step is None for _, step in events[1:]))
        self.graph._resolve_pending_entries.assert_called_once_with(self.ticker)
        self.graph.resolve_instrument_context.assert_called_once_with(
            self.ticker, "stock", self.date
        )
        self.graph.memory_log.store_decision.assert_called_once()
        self.assertFalse(
            has_checkpoint(self.tmpdir, self.ticker, self.date, signature)
        )
        self.assertIsNone(self.graph._checkpointer_ctx)

    def test_chunk_callback_failure_keeps_checkpoint_and_skips_memory(self):
        self.should_crash["value"] = False

        def fail_after_analyst(state, _step):
            if state["count"] == 1:
                raise RuntimeError("display failed")

        with self.assertRaisesRegex(RuntimeError, "display failed"):
            self.graph.propagate(
                self.ticker,
                self.date,
                on_chunk=fail_after_analyst,
            )

        signature = self.graph._run_signature("stock")
        self.assertTrue(
            has_checkpoint(self.tmpdir, self.ticker, self.date, signature)
        )
        self.graph.memory_log.store_decision.assert_not_called()
        self.graph._log_state.assert_not_called()
        self.assertIsNone(self.graph._checkpointer_ctx)


if __name__ == "__main__":
    unittest.main()
