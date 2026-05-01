#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tick_penetration_lattice_core as tick_core


def fake_tick_pnl(_symbol: str, direction: str, entry: float, exit_price: float, volume: float = 0.01) -> float:
    return round((exit_price - entry) if direction == "BUY" else (entry - exit_price), 6)


class TickNativePhase1TelemetryTests(unittest.TestCase):
    def _make_stateful_engine(self) -> tick_core.TickStatefulRearmEngine:
        cfg = SimpleNamespace(step_pips=0.5, max_open_per_side=5, step_is_price_units=True)
        info = SimpleNamespace(point=1.0, spread=0.01)
        with (
            patch.object(tick_core, "pip_size_for", return_value=1.0),
            patch.object(tick_core, "spread_price", return_value=0.01),
        ):
            engine = tick_core.TickStatefulRearmEngine(
                "TEST",
                cfg,
                info,
                timeframe_name="M1",
                variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc2"],
                cooldown_bars=0,
                sell_gap=1,
                buy_gap=1,
            )
        engine.state.anchor = 100.0
        engine.state.next_sell_level = 100.5
        engine.state.next_buy_level = 99.5
        return engine

    def _ticks(self) -> list[dict[str, float | int]]:
        return [
            {"time": 36000, "time_msc": 36000000, "bid": 100.50, "ask": 100.51},
            {"time": 36010, "time_msc": 36010000, "bid": 101.00, "ask": 101.01},
            {"time": 36020, "time_msc": 36020000, "bid": 100.48, "ask": 100.49},
            {"time": 36030, "time_msc": 36030000, "bid": 100.00, "ask": 100.01},
            {"time": 36040, "time_msc": 36040000, "bid": 101.00, "ask": 101.01},
        ]

    def test_stateful_events_include_phase1_close_and_rearm_fields(self) -> None:
        engine = self._make_stateful_engine()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            event_path = Path(tmpdir) / "events.jsonl"
            for tick in self._ticks():
                engine.process_tick(tick, action_sink=None, event_path=event_path, emit=True)

            records = [
                json.loads(line)
                for line in event_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        close_events = [record for record in records if record.get("action") == "close_ticket"]
        rearm_open_events = [record for record in records if record.get("action") == "open_ticket" and record.get("rearm_open")]

        self.assertTrue(close_events)
        self.assertTrue(rearm_open_events)

        close_event = close_events[0]
        self.assertEqual(close_event["hold_seconds"], 10)
        self.assertEqual(close_event["time_to_first_green_seconds"], 10)
        self.assertGreater(close_event["max_favorable_excursion_pnl"], 0.0)
        self.assertLessEqual(close_event["max_adverse_excursion_pnl"], 0.0)
        self.assertFalse(close_event["first_green_before_fail"])
        self.assertEqual(close_event["entry_context"], "main|good_session|tight_spread")
        self.assertTrue(close_event["reclaimed_trigger_level_seen"])
        self.assertTrue(close_event["retraced_0_25x_step_seen"])
        self.assertTrue(close_event["retraced_0_5x_step_seen"])

        rearm_event = rearm_open_events[0]
        self.assertEqual(rearm_event["entry_context"], "rearm|good_session|tight_spread")
        self.assertEqual(rearm_event["token_age_at_fire_seconds"], 20)
        self.assertEqual(rearm_event["armed_duration_seconds"], 10)
        self.assertEqual(rearm_event["same_tick_open_burst_count"], 1)

    def test_phase1_telemetry_is_behavior_invariant(self) -> None:
        engine_with_events = self._make_stateful_engine()
        engine_without_events = self._make_stateful_engine()

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            event_path = Path(tmpdir) / "events.jsonl"
            for tick in self._ticks():
                engine_with_events.process_tick(tick, action_sink=None, event_path=event_path, emit=True)
                engine_without_events.process_tick(tick, action_sink=None, event_path=None, emit=False)

        self.assertEqual(engine_with_events.snapshot(), engine_without_events.snapshot())


if __name__ == "__main__":
    unittest.main()
