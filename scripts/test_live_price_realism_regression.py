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

import live_penetration_lattice_shadow as shadow
from penetration_lattice_hybrid_apex import RawConfig


class LivePriceRealismRegressionTests(unittest.TestCase):
    def test_bar_reaches_price_level_requires_ask_touch_for_buy_under_broker_touch(self) -> None:
        buy_bar = {"high": 100.0, "low": 89.5, "close": 95.0}
        self.assertTrue(
            shadow._bar_reaches_price_level("BUY", 90.0, buy_bar, spread_px=0.0, mode="intrabar", purpose="open")
        )
        self.assertFalse(
            shadow._bar_reaches_price_level("BUY", 90.0, buy_bar, spread_px=1.0, mode="broker_touch", purpose="open")
        )
        self.assertFalse(
            shadow._bar_reaches_price_level("SELL", 90.0, {"high": 92.0, "low": 89.8, "close": 90.5}, spread_px=1.1, mode="broker_touch", purpose="close")
        )

    def test_apply_close_realism_uses_bar_close_for_live_sell_and_buy(self) -> None:
        sell_bar = {"close": 110.0}
        buy_bar = {"close": 90.0}
        self.assertEqual(shadow._apply_close_realism("SELL", 85.0, sell_bar, "bar_close"), 110.0)
        self.assertEqual(shadow._apply_close_realism("BUY", 115.0, buy_bar, "bar_close"), 90.0)
        self.assertEqual(shadow._apply_close_realism("SELL", 85.0, sell_bar, "intrabar"), 85.0)

    def test_raw_engine_broker_touch_skips_buy_if_only_bid_low_reaches_level(self) -> None:
        info = SimpleNamespace(point=1.0, spread=1.0)
        cfg = RawConfig(step_pips=10.0, max_open_per_side=5, close_mode="two_level")
        with (
            patch.object(shadow, "pip_size_for", return_value=1.0),
            patch.object(shadow, "spread_price", return_value=1.0),
        ):
            engine = shadow.RawClose2Engine(
                "BTCUSD",
                cfg,
                info,
                close_alpha=0.0,
                close_realism_mode="bar_close",
                open_realism_mode="broker_touch",
            )
            engine.process_bar({"time": 1, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "tick_volume": 1})
            engine.process_bar({"time": 2, "open": 100.0, "high": 105.0, "low": 89.5, "close": 95.0, "tick_volume": 1})

        self.assertEqual(engine.state.open_tickets, [])
        self.assertEqual(engine.state.next_buy_level, 90.0)

    def test_raw_engine_bar_close_realism_clamps_optimistic_extreme_exit(self) -> None:
        info = SimpleNamespace(point=1.0, spread=0.0)
        cfg = RawConfig(step_pips=10.0, max_open_per_side=5, close_mode="two_level")

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, _spread_px: float) -> float:
            if direction == "SELL":
                return entry - exit_price
            return exit_price - entry

        with (
            patch.object(shadow, "pip_size_for", return_value=1.0),
            patch.object(shadow, "spread_price", return_value=0.0),
            patch.object(shadow, "unit_pnl_usd", side_effect=fake_pnl),
        ):
            engine = shadow.RawClose2Engine(
                "BTCUSD",
                cfg,
                info,
                close_alpha=1.0,
                close_realism_mode="bar_close",
            )
            engine.process_bar({"time": 1, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "tick_volume": 1})
            engine.process_bar({"time": 2, "open": 100.0, "high": 130.0, "low": 85.0, "close": 110.0, "tick_volume": 1})

        self.assertEqual(engine.state.realized_closes, 1)
        self.assertEqual(engine.state.realized_net_usd, 20.0)


if __name__ == "__main__":
    unittest.main()
