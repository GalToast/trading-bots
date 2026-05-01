#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
import tick_penetration_lattice_core as tick_core


class TickNativeSameBarRegressionTests(unittest.TestCase):
    def _make_engine(self, *, same_bar_min_pnl: float, shallow_level_cap: int) -> tick_core.TickBoundedRearmEngine:
        info = SimpleNamespace(point=0.01, spread=1.0)
        cfg = BoundedConfig(
            step_pips=0.5,
            max_open_per_side=20,
            max_floating_loss_usd=-10.0,
            vwap_lookback=20,
            regime_lookback_bars=60,
            max_range_pips=24.0,
            breakout_buffer_pips=5.0,
            max_lattice_window_bars=240,
            cooldown_bars=60,
        )
        with (
            patch.object(tick_core, "pip_size_for", return_value=0.01),
            patch.object(tick_core, "spread_price", return_value=0.01),
        ):
            engine = tick_core.TickBoundedRearmEngine(
                "USDJPY",
                cfg,
                info,
                timeframe_name="M1",
                variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc2"],
                close_gap=1,
                same_bar_min_pnl=same_bar_min_pnl,
                same_bar_shallow_level_cap=shallow_level_cap,
            )
        engine.state.anchor = 100.0
        engine.state.next_sell_level = 100.5
        engine.state.next_buy_level = 99.5
        return engine

    def test_same_bar_shallow_buy_close_is_blocked_below_threshold(self) -> None:
        engine = self._make_engine(same_bar_min_pnl=0.06, shallow_level_cap=2)
        engine.state.open_tickets = [
            {
                "direction": "BUY",
                "trigger_level": 99.90,
                "fill_price": 99.90,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 1,
            },
            {
                "direction": "BUY",
                "trigger_level": 99.95,
                "fill_price": 99.95,
                "opened_time": 110,
                "opened_msc": 110000,
                "level_idx": 1,
            },
        ]
        tick = {"time": 118, "time_msc": 118000, "bid": 100.0, "ask": 100.01, "last": 100.0, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            return round((exit_price - entry) if direction == "BUY" else (entry - exit_price), 4)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        remaining = sorted(ticket["trigger_level"] for ticket in engine.state.open_tickets)
        self.assertEqual(remaining, [99.95])
        self.assertEqual(engine.state.realized_closes, 1)

    def test_deeper_same_bar_buy_can_still_close(self) -> None:
        engine = self._make_engine(same_bar_min_pnl=0.06, shallow_level_cap=1)
        engine.state.open_tickets = [
            {
                "direction": "BUY",
                "trigger_level": 99.90,
                "fill_price": 99.90,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 1,
            },
            {
                "direction": "BUY",
                "trigger_level": 99.95,
                "fill_price": 99.95,
                "opened_time": 110,
                "opened_msc": 110000,
                "level_idx": 2,
            },
        ]
        tick = {"time": 118, "time_msc": 118000, "bid": 100.0, "ask": 100.01, "last": 100.0, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            return round((exit_price - entry) if direction == "BUY" else (entry - exit_price), 4)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        self.assertEqual(engine.state.open_tickets, [])
        self.assertEqual(engine.state.realized_closes, 2)


if __name__ == "__main__":
    unittest.main()
