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


class TickBoundedAdaptiveHardeningTests(unittest.TestCase):
    def _make_engine(
        self,
        *,
        max_floating_loss_usd: float = -1.0,
        breakout_buffer_pips: float = 5.0,
        cluster_aware_escape: bool = False,
        guard_open_admission: bool = False,
        suppress_additional_levels_after_burst: bool = False,
        burst_open_threshold: int = 2,
        max_entry_spread_ratio: float = 0.0,
        adaptive_overlay_autopilot: bool = False,
        min_positive_close_profit_usd: float = 0.0,
        positive_only_closes: bool = False,
    ) -> tick_core.TickBoundedRearmEngine:
        info = SimpleNamespace(point=0.01, spread=1.0)
        cfg = BoundedConfig(
            step_pips=0.5,
            max_open_per_side=20,
            max_floating_loss_usd=max_floating_loss_usd,
            vwap_lookback=20,
            regime_lookback_bars=60,
            max_range_pips=24.0,
            breakout_buffer_pips=breakout_buffer_pips,
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
                cluster_aware_escape=cluster_aware_escape,
                guard_open_admission=guard_open_admission,
                suppress_additional_levels_after_burst=suppress_additional_levels_after_burst,
                burst_open_threshold=burst_open_threshold,
                max_entry_spread_ratio=max_entry_spread_ratio,
                adaptive_overlay_autopilot=adaptive_overlay_autopilot,
                min_positive_close_profit_usd=min_positive_close_profit_usd,
                positive_only_closes=positive_only_closes,
            )
        engine.state.anchor = 100.0
        engine.state.next_sell_level = 100.005
        engine.state.next_buy_level = 99.995
        return engine

    def test_bounded_autopilot_arms_after_burst_and_suppresses_extra_opens(self) -> None:
        engine = self._make_engine(adaptive_overlay_autopilot=True, burst_open_threshold=2)
        tick = {"time": 120, "time_msc": 120000, "bid": 100.020, "ask": 100.030, "last": 100.025, "flags": 0, "volume": 1, "volume_real": 1.0}

        engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        snapshot = engine.snapshot()
        self.assertEqual(len(snapshot["open_tickets"]), 2)
        self.assertTrue(snapshot["adaptive_overlay_autopilot"])
        self.assertTrue(snapshot["adaptive_overlay_autopilot_triggered"])
        self.assertEqual(snapshot["adaptive_overlay_autopilot_reason"], "burst_concentration_detected")
        self.assertTrue(snapshot["guard_open_admission"])
        self.assertTrue(snapshot["cluster_aware_escape"])
        self.assertTrue(snapshot["suppress_additional_levels_after_burst"])

    def test_bounded_spread_gate_blocks_toxic_open(self) -> None:
        engine = self._make_engine(max_entry_spread_ratio=0.3)
        tick = {"time": 120, "time_msc": 120000, "bid": 100.005, "ask": 100.015, "last": 100.010, "flags": 0, "volume": 1, "volume_real": 1.0}

        engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        snapshot = engine.snapshot()
        self.assertEqual(len(snapshot["open_tickets"]), 0)
        self.assertAlmostEqual(float(snapshot["max_entry_spread_ratio"]), 0.3)

    def test_bounded_load_snapshot_preserves_current_spread_gate_contract(self) -> None:
        engine = self._make_engine(max_entry_spread_ratio=1.2, positive_only_closes=True)

        engine.load_snapshot(
            {
                "max_entry_spread_ratio": 0.3,
                "positive_only_closes": False,
                "open_tickets": [],
                "rearm_tokens": [],
            }
        )

        snapshot = engine.snapshot()
        self.assertAlmostEqual(float(snapshot["max_entry_spread_ratio"]), 1.2)
        self.assertTrue(snapshot["positive_only_closes"])

    def test_bounded_burst_suppression_is_same_side_only_and_keeps_reversal_entry_available(self) -> None:
        engine = self._make_engine(suppress_additional_levels_after_burst=True, burst_open_threshold=2)

        with patch.object(tick_core, "tick_pnl_usd", return_value=-0.5):
            engine.process_tick(
                {"time": 120, "time_msc": 120000, "bid": 100.020, "ask": 100.030, "last": 100.025, "flags": 0, "volume": 1, "volume_real": 1.0},
                action_sink=None,
                event_path=None,
                emit=False,
            )
            engine.process_tick(
                {"time": 130, "time_msc": 130000, "bid": 99.970, "ask": 99.980, "last": 99.975, "flags": 0, "volume": 1, "volume_real": 1.0},
                action_sink=None,
                event_path=None,
                emit=False,
            )

        directions = sorted(ticket["direction"] for ticket in engine.state.open_tickets)
        self.assertEqual(directions, ["BUY", "BUY", "SELL", "SELL"])

    def test_bounded_cluster_escape_cuts_worst_same_fill_cluster_before_global_kill(self) -> None:
        engine = self._make_engine(max_floating_loss_usd=-0.5, breakout_buffer_pips=100.0, cluster_aware_escape=True)
        engine.state.next_sell_level = 101.0
        engine.state.next_buy_level = 99.0
        engine.state.open_tickets = [
            {"direction": "BUY", "trigger_level": 99.90, "fill_price": 100.00, "opened_time": 60, "opened_msc": 60000, "level_idx": 1},
            {"direction": "BUY", "trigger_level": 99.85, "fill_price": 100.00, "opened_time": 61, "opened_msc": 61000, "level_idx": 2},
            {"direction": "SELL", "trigger_level": 100.10, "fill_price": 100.20, "opened_time": 62, "opened_msc": 62000, "level_idx": 1},
        ]
        tick = {"time": 180, "time_msc": 180000, "bid": 99.45, "ask": 99.46, "last": 99.455, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            if direction == "BUY":
                return round(exit_price - entry, 3)
            return round(entry - exit_price, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        remaining = engine.state.open_tickets
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["direction"], "SELL")
        self.assertEqual(engine.state.realized_closes, 2)
        self.assertEqual(engine.state.anchor_resets_risk, 0)

    def test_bounded_guard_open_admission_requires_frontier_recovery(self) -> None:
        engine = self._make_engine(guard_open_admission=True)
        tickets = [
            tick_core.deserialize_tick_ticket(
                {
                    "direction": "SELL",
                    "trigger_level": 100.5,
                    "fill_price": 100.5,
                    "opened_time": 60,
                    "opened_msc": 60000,
                    "level_idx": 1,
                    "first_green_seen": True,
                    "reclaimed_trigger_level_seen": True,
                }
            ),
            tick_core.deserialize_tick_ticket(
                {
                    "direction": "SELL",
                    "trigger_level": 101.0,
                    "fill_price": 101.0,
                    "opened_time": 61,
                    "opened_msc": 61000,
                    "level_idx": 2,
                    "first_green_seen": False,
                    "reclaimed_trigger_level_seen": False,
                }
            ),
        ]

        allowed, recovered_count = engine._guard_open_admission_allows(tickets, "SELL")

        self.assertFalse(allowed)
        self.assertEqual(recovered_count, 1)

    def test_bounded_engine_exposes_close_at_float_zero_flag_even_when_disabled(self) -> None:
        engine = self._make_engine()
        snapshot = engine.snapshot()

        self.assertFalse(engine.close_at_float_zero)
        self.assertIn("close_at_float_zero", snapshot)
        self.assertFalse(snapshot["close_at_float_zero"])

    def test_bounded_engine_blocks_ordinary_close_below_positive_buffer(self) -> None:
        engine = self._make_engine(min_positive_close_profit_usd=0.5)
        engine.state.anchor = 100.0
        engine.state.next_sell_level = 101.0
        engine.state.next_buy_level = 99.0
        engine.state.open_tickets = [
            {
                "direction": "SELL",
                "trigger_level": 100.30,
                "fill_price": 100.30,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 2,
            },
            {
                "direction": "SELL",
                "trigger_level": 100.10,
                "fill_price": 100.10,
                "opened_time": 61,
                "opened_msc": 61000,
                "level_idx": 1,
            },
        ]
        tick = {"time": 180, "time_msc": 180000, "bid": 100.04, "ask": 100.05, "last": 100.045, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            if direction == "SELL":
                return round(entry - exit_price, 3)
            return round(exit_price - entry, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        self.assertEqual(engine.state.realized_closes, 0)
        self.assertEqual(len(engine.state.open_tickets), 2)
        self.assertAlmostEqual(engine.snapshot()["min_positive_close_profit_usd"], 0.5)


if __name__ == "__main__":
    unittest.main()
