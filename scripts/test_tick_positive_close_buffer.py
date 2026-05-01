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

import tick_penetration_lattice_core as tick_core
from penetration_lattice_hybrid_apex import RawConfig, V3Config


class TickPositiveCloseBufferTests(unittest.TestCase):
    def _make_stateful_engine(
        self,
        *,
        min_positive_close_profit_usd: float,
        max_entry_spread_ratio: float = 0.0,
        guard_open_admission: bool = False,
        suppress_additional_levels_after_burst: bool = False,
        burst_open_threshold: int = 2,
        adaptive_overlay_autopilot: bool = False,
    ) -> tick_core.TickStatefulRearmEngine:
        info = SimpleNamespace(point=0.0001, spread=1.0)
        cfg = RawConfig(step_pips=2.0, max_open_per_side=20, close_mode="two_level")
        with (
            patch.object(tick_core, "pip_size_for", return_value=0.0001),
            patch.object(tick_core, "spread_price", return_value=0.0001),
        ):
            engine = tick_core.TickStatefulRearmEngine(
                "GBPUSD",
                cfg,
                info,
                timeframe_name="M1",
                variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
                close_alpha=0.5,
                close_style="all_profitable",
                sell_gap=1,
                buy_gap=1,
                min_positive_close_profit_usd=min_positive_close_profit_usd,
                max_entry_spread_ratio=max_entry_spread_ratio,
                guard_open_admission=guard_open_admission,
                suppress_additional_levels_after_burst=suppress_additional_levels_after_burst,
                burst_open_threshold=burst_open_threshold,
                adaptive_overlay_autopilot=adaptive_overlay_autopilot,
            )
        engine.state.anchor = 1.3500
        engine.state.next_sell_level = 1.3600
        engine.state.next_buy_level = 1.3400
        return engine

    def _make_bounded_engine(
        self,
        *,
        min_positive_close_profit_usd: float,
        max_entry_spread_ratio: float = 0.0,
    ) -> tick_core.TickBoundedRearmEngine:
        info = SimpleNamespace(point=0.0001, spread=1.0)
        cfg = V3Config(step_pips=2.0, max_open_per_side=20)
        with (
            patch.object(tick_core, "pip_size_for", return_value=0.0001),
            patch.object(tick_core, "spread_price", return_value=0.0001),
        ):
            engine = tick_core.TickBoundedRearmEngine(
                "USDJPY",
                cfg,
                info,
                timeframe_name="M1",
                variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
                close_gap=1,
                close_style="all_profitable",
                min_positive_close_profit_usd=min_positive_close_profit_usd,
                max_entry_spread_ratio=max_entry_spread_ratio,
                positive_only_closes=True,
            )
        engine.state.anchor = 1.3500
        engine.state.next_sell_level = 1.33995
        engine.state.next_buy_level = 1.34005
        return engine

    def test_stateful_engine_blocks_scratch_positive_close_below_buffer(self) -> None:
        engine = self._make_stateful_engine(min_positive_close_profit_usd=0.0060)
        engine.state.open_tickets = [
            {
                "direction": "SELL",
                "trigger_level": 1.3506,
                "fill_price": 1.3506,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 2,
            },
            {
                "direction": "SELL",
                "trigger_level": 1.3502,
                "fill_price": 1.3502,
                "opened_time": 61,
                "opened_msc": 61000,
                "level_idx": 1,
            },
        ]
        tick = {"time": 180, "time_msc": 180000, "bid": 1.3500, "ask": 1.35005, "last": 1.35002, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            if direction == "SELL":
                return round(entry - exit_price, 5)
            return round(exit_price - entry, 5)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        self.assertEqual(engine.state.realized_closes, 0)
        self.assertEqual(len(engine.state.open_tickets), 2)
        self.assertAlmostEqual(engine.snapshot()["min_positive_close_profit_usd"], 0.0060)

    def test_positive_only_closes_turns_negative_forced_unwind_into_hold(self) -> None:
        engine = self._make_stateful_engine(min_positive_close_profit_usd=0.0)
        engine.positive_only_closes = True
        engine.state.positive_only_closes = True
        engine.max_floating_loss_usd = -0.0010
        engine.state.open_tickets = [
            {
                "direction": "SELL",
                "trigger_level": 1.3506,
                "fill_price": 1.3506,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 2,
            }
        ]
        tick = {"time": 180, "time_msc": 180000, "bid": 1.3515, "ask": 1.3516, "last": 1.35155, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            if direction == "SELL":
                return round(entry - exit_price, 5)
            return round(exit_price - entry, 5)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        self.assertEqual(engine.state.realized_closes, 0)
        self.assertEqual(len(engine.state.open_tickets), 1)
        self.assertTrue(engine.state.positive_only_hold_active)
        self.assertIn("blocked_negative", engine.state.positive_only_hold_reason)

    def test_stateful_hold_allows_only_opposite_side_open_for_one_sided_book(self) -> None:
        engine = self._make_stateful_engine(min_positive_close_profit_usd=0.0)
        engine.positive_only_closes = True
        engine.state.positive_only_closes = True
        engine.state.positive_only_hold_active = True
        engine.state.positive_only_hold_reason = "forced_unwind_blocked_negative"
        engine.state.next_sell_level = 1.33995
        engine.state.next_buy_level = 1.34005
        engine.state.open_tickets = [
            {
                "direction": "SELL",
                "trigger_level": 1.3506,
                "fill_price": 1.3506,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 2,
            }
        ]
        tick = {"time": 180, "time_msc": 180000, "bid": 1.3400, "ask": 1.3400, "last": 1.3400, "flags": 0, "volume": 1, "volume_real": 1.0}

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            if direction == "SELL":
                return round(entry - exit_price, 5)
            return round(exit_price - entry, 5)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=None, event_path=None, emit=False)

        directions = [ticket["direction"] for ticket in engine.state.open_tickets]
        self.assertEqual(directions.count("SELL"), 1)
        self.assertEqual(directions.count("BUY"), 1)

    def test_bounded_hold_allows_only_opposite_side_open_for_one_sided_book(self) -> None:
        engine = self._make_bounded_engine(min_positive_close_profit_usd=0.0)
        engine.state.positive_only_hold_active = True
        engine.state.positive_only_hold_reason = "bounded_forced_unwind_blocked_negative"
        engine.state.open_tickets = [
            {
                "direction": "SELL",
                "trigger_level": 1.3506,
                "fill_price": 1.3506,
                "opened_time": 60,
                "opened_msc": 60000,
                "level_idx": 2,
            }
        ]
        tick = {"time": 180, "time_msc": 180000, "bid": 1.3400, "ask": 1.3400, "last": 1.3400, "flags": 0, "volume": 1, "volume_real": 1.0}
        actions: list[dict[str, object]] = []

        def fake_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
            if direction == "SELL":
                return round(entry - exit_price, 5)
            return round(exit_price - entry, 5)

        def action_sink(request: dict[str, object]) -> dict[str, object]:
            actions.append(dict(request))
            return {"ok": True, "fill_price": float(request["fill_price"])}

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_pnl):
            engine.process_tick(tick, action_sink=action_sink, event_path=None, emit=False)

        open_directions = [str(action["direction"]) for action in actions if str(action.get("kind")) == "open"]
        self.assertEqual(open_directions, ["BUY"])

    def test_stateful_engine_load_snapshot_preserves_configured_live_contract_fields(self) -> None:
        engine = self._make_stateful_engine(
            min_positive_close_profit_usd=1.0,
            max_entry_spread_ratio=15.0,
            guard_open_admission=True,
            suppress_additional_levels_after_burst=True,
            burst_open_threshold=2,
            adaptive_overlay_autopilot=True,
        )
        engine.positive_only_closes = True
        engine.state.positive_only_closes = True

        engine.load_snapshot(
            {
                "max_entry_spread_ratio": 0.3,
                "min_positive_close_profit_usd": 0.0,
                "positive_only_closes": False,
                "guard_open_admission": False,
                "suppress_additional_levels_after_burst": False,
                "burst_open_threshold": 9,
                "adaptive_overlay_autopilot": False,
            }
        )

        snapshot = engine.snapshot()
        self.assertEqual(snapshot["max_entry_spread_ratio"], 15.0)
        self.assertEqual(snapshot["min_positive_close_profit_usd"], 1.0)
        self.assertTrue(snapshot["positive_only_closes"])
        self.assertTrue(snapshot["guard_open_admission"])
        self.assertTrue(snapshot["suppress_additional_levels_after_burst"])
        self.assertEqual(snapshot["burst_open_threshold"], 2)
        self.assertTrue(snapshot["adaptive_overlay_autopilot"])


if __name__ == "__main__":
    unittest.main()
