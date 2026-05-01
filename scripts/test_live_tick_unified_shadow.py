#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_penetration_lattice_tick_unified_shadow as unified_tick


class LiveTickUnifiedShadowTests(unittest.TestCase):
    def test_step_to_price_converts_fx_pips_when_flag_false(self) -> None:
        with (
            patch.object(unified_tick.mt5, "symbol_info", return_value=object()),
            patch.object(unified_tick, "pip_size_for", return_value=0.0001),
        ):
            step_px = unified_tick.step_to_price(
                "GBPUSD",
                {
                    "timeframe": "M1",
                    "step": 1.0,
                    "step_is_price_units": False,
                },
            )
        self.assertAlmostEqual(step_px, 0.0001)

    def test_step_to_price_keeps_crypto_price_units_when_flag_true(self) -> None:
        with patch.object(unified_tick.mt5, "symbol_info", return_value=object()):
            step_px = unified_tick.step_to_price(
                "BTCUSD",
                {
                    "timeframe": "H1",
                    "step": 50.0,
                    "step_is_price_units": True,
                },
            )
        self.assertEqual(step_px, 50.0)

    def test_build_engine_passes_gap_alpha_and_step_price(self) -> None:
        captured: dict[str, object] = {}

        def fake_engine_from_args(**kwargs):
            captured.update(kwargs)
            return object()

        with (
            patch.object(unified_tick.mt5, "symbol_info", return_value=object()),
            patch.object(unified_tick, "pip_size_for", return_value=0.0001),
            patch.object(unified_tick, "engine_from_args", side_effect=fake_engine_from_args),
        ):
            engine = unified_tick.build_engine(
                "EURUSD",
                {
                    "timeframe": "M1",
                    "step": 0.5,
                    "step_is_price_units": False,
                    "max_open_per_side": 20,
                    "close_gap": 3,
                    "close_alpha": 0.5,
                    "momentum_gate": True,
                    "rearm_variant": "rearm_lvl2_exc1",
                },
            )

        self.assertIsNotNone(engine)
        self.assertEqual(captured["timeframe_name"], "M1")
        self.assertEqual(captured["step"], 0.00005)
        self.assertEqual(captured["sell_gap"], 3)
        self.assertEqual(captured["buy_gap"], 3)
        self.assertEqual(captured["close_alpha"], 0.5)
        self.assertTrue(captured["momentum_gate"])

    def test_run_once_falls_back_to_live_tick_when_history_is_empty(self) -> None:
        processed: list[dict] = []
        engine = SimpleNamespace(
            symbol="BTCUSD",
            timeframe_name="H1",
            state=SimpleNamespace(last_tick_msc=1775967685560),
        )
        engine.process_ticks = lambda ticks, **_kwargs: processed.extend(ticks) or len(ticks)
        runner_status = {"heartbeat_at": None, "last_successful_run_at": None, "consecutive_exceptions": 7}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            with (
                patch.object(unified_tick, "load_ticks_since", return_value=[]),
                patch.object(
                    unified_tick,
                    "load_current_tick",
                    return_value={
                        "time": 1775978407,
                        "time_msc": 1775978407405,
                        "bid": 71469.29,
                        "ask": 71639.72,
                        "last": 0.0,
                        "flags": 0,
                        "volume": 0,
                        "volume_real": 0.0,
                    },
                ),
                patch.object(unified_tick.mt5, "symbol_info", return_value=object()),
                patch.object(unified_tick, "append_jsonl") as append_jsonl,
                patch.object(unified_tick, "save_state") as save_state,
            ):
                unified_tick.run_once(
                    {"BTCUSD": engine},
                    {"BTCUSD": {"timeframe": "H1", "step": 50.0}},
                    state_dir=state_dir,
                    runner_status=runner_status,
                )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["time_msc"], 1775978407405)
        self.assertEqual(runner_status["consecutive_exceptions"], 0)
        save_state.assert_called_once()
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "tick_history_fallback")
        self.assertEqual(logged["live_tick_msc"], 1775978407405)


if __name__ == "__main__":
    unittest.main()
