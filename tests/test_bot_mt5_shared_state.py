"""Shared-state rebinding across the bot/mt5 package split.

Regression tests for the split-brain bug fixed in the "route shared state
through bot.mt5.state" change: names that are *rebound* at runtime
(mt5_connected, _brain, _learner, _tick_cache_cycle, consecutive_wins,
consecutive_losses, total_pnl, trades) must be single-sourced in
bot.mt5.state, with the mt5_bot_v10 shim delegating live (PEP 562).

Run from the repo root with:
  MT5_LOGIN=12345 MT5_PASSWORD=dummy MT5_SERVER=dummy \\
  PYTHONPATH=tests/ci_stubs python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# mt5_config.py requires credentials at import time; the shim under test pulls
# it in, so provide dummy values here (MetaTrader5 itself is stubbed via
# PYTHONPATH=tests/ci_stubs, and CI sets no real credentials).
os.environ.setdefault("MT5_LOGIN", "12345")
os.environ.setdefault("MT5_PASSWORD", "dummy")
os.environ.setdefault("MT5_SERVER", "dummy")

import MetaTrader5 as mt5_stub  # the CI stub module object (shared with bot code)

import mt5_bot_v10 as shim
from bot.mt5 import market_data, runner, state


class SharedStateRebindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._snapshot = {
            "_brain": state._brain,
            "_learner": state._learner,
            "_tick_cache_cycle": state._tick_cache_cycle,
            "mt5_connected": state.mt5_connected,
            "consecutive_wins": state.consecutive_wins,
            "consecutive_losses": state.consecutive_losses,
            "total_pnl": state.total_pnl,
            "trades": state.trades,
        }

    def tearDown(self) -> None:
        for k, v in self._snapshot.items():
            setattr(state, k, v)
        state._tick_cache.clear()
        state._bars_cache.pop("probe", None)
        state.active_positions.pop("T1", None)

    def test_get_brain_single_sources_state(self) -> None:
        brain = market_data.get_brain()
        self.assertIsNotNone(brain)
        self.assertIs(state._brain, brain)
        self.assertIs(shim._brain, brain)  # shim delegates live, not a frozen import
        self.assertFalse(hasattr(market_data, "_brain"))  # no per-module copy anymore

    def test_get_learner_single_sources_state(self) -> None:
        learner = market_data.get_learner()
        self.assertIsNotNone(learner)
        self.assertIs(state._learner, learner)
        self.assertIs(shim._learner, learner)
        self.assertFalse(hasattr(market_data, "_learner"))

    def test_connect_mt5_rebinds_shared_flag(self) -> None:
        class _FakeInfo:
            login = 12345

        mt5_stub.initialize = lambda **kw: True
        mt5_stub.account_info = lambda: _FakeInfo()
        mt5_stub.last_error = lambda: (0, "ok")

        self.assertTrue(market_data.connect_mt5())
        self.assertTrue(state.mt5_connected)
        self.assertTrue(shim.mt5_connected)  # was permanently False before the fix
        self.assertFalse(hasattr(market_data, "mt5_connected"))
        self.assertTrue(market_data.ensure_mt5())  # reads the same shared flag

    def test_tick_cache_cycle_rebinds_shared_counter(self) -> None:
        mt5_stub.symbol_info_tick = lambda s: ("tick", s)
        market_data.refresh_tick_cache_for_cycle(["EURUSD"], 7)
        self.assertEqual(state._tick_cache_cycle, 7)
        self.assertEqual(shim._tick_cache_cycle, 7)
        self.assertFalse(hasattr(market_data, "_tick_cache_cycle"))
        # the dict itself is still the shared object, mutated in place
        self.assertIs(market_data._tick_cache, state._tick_cache)
        self.assertEqual(market_data.get_tick_cached("EURUSD", 7), ("tick", "EURUSD"))

    def test_run_counters_rebind_shared_state(self) -> None:
        # run()'s rebinding statements execute in run.__globals__; emulate them
        # exactly as the fixed run() does (explicit state.X, no `global` decl).
        self.assertNotIn("consecutive_wins", runner.run.__globals__)
        self.assertNotIn("total_pnl", runner.run.__globals__)
        self.assertNotIn("trades", runner.run.__globals__)
        exec(
            "state.consecutive_wins = 3\n"
            "state.consecutive_losses = 1\n"
            "state.total_pnl = 12.5\n"
            "state.trades = 9\n",
            runner.run.__globals__,
        )
        for view in (state, shim):
            self.assertEqual(view.consecutive_wins, 3)
            self.assertEqual(view.consecutive_losses, 1)
            self.assertEqual(view.total_pnl, 12.5)
            self.assertEqual(view.trades, 9)

    def test_dict_state_still_shared_by_mutation(self) -> None:
        market_data._bars_cache["probe"] = (0, [])
        self.assertEqual(state._bars_cache.get("probe"), (0, []))
        from bot.mt5 import book

        book.active_positions["T1"] = {"symbol": "EURUSD"}
        self.assertEqual(state.active_positions.get("T1"), {"symbol": "EURUSD"})
        self.assertEqual(
            sys.modules["bot.mt5.journal"].active_positions.get("T1"),
            {"symbol": "EURUSD"},
        )

    def test_shim_namespace_complete(self) -> None:
        live = {
            "_brain",
            "_learner",
            "_tick_cache_cycle",
            "consecutive_losses",
            "consecutive_wins",
            "mt5_connected",
            "total_pnl",
            "trades",
        }
        names = {n for n in dir(shim) if not n.startswith("__")}
        self.assertTrue(live <= names)
        for n in live:
            self.assertIs(getattr(shim, n), getattr(state, n))


if __name__ == "__main__":
    unittest.main()
