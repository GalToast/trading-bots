#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_penetration_lattice_tick_shadow as tick_shadow
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig


class LiveTickShadowOverrideRegressionTests(unittest.TestCase):
    def test_load_raw_symbol_overrides_keeps_max_entry_spread_ratio(self) -> None:
        payload_path = Path("test_raw_symbol_overrides_tmp.json")
        payload_path.write_text(
            '{"NZDUSD":{"max_entry_spread_ratio":0.2,"raw_close_alpha":0.8,"step_buy":0.0004,"step_sell":0.0002,"min_positive_close_profit_usd":0.25,"offensive_closure_enabled":true}}',
            encoding="utf-8",
        )
        try:
            overrides = tick_shadow.load_raw_symbol_overrides(payload_path)
        finally:
            payload_path.unlink(missing_ok=True)

        self.assertEqual(set(overrides.keys()), {"NZDUSD"})
        self.assertAlmostEqual(float(overrides["NZDUSD"]["max_entry_spread_ratio"]), 0.2)
        self.assertAlmostEqual(float(overrides["NZDUSD"]["raw_close_alpha"]), 0.8)
        self.assertAlmostEqual(float(overrides["NZDUSD"]["step_buy"]), 0.0004)
        self.assertAlmostEqual(float(overrides["NZDUSD"]["step_sell"]), 0.0002)
        self.assertAlmostEqual(float(overrides["NZDUSD"]["min_positive_close_profit_usd"]), 0.25)
        self.assertTrue(bool(overrides["NZDUSD"]["offensive_closure_enabled"]))

    def test_build_engines_passes_raw_close_alpha_to_raw_engine(self) -> None:
        captured: dict[str, object] = {}

        def fake_engine_from_args(**kwargs):
            captured.update(kwargs)
            return object()

        with (
            patch.object(
                tick_shadow,
                "default_apex_mix",
                return_value={
                    "EURUSD": (
                        "raw_close2",
                        tick_shadow.RawConfig(step_pips=3.0, max_open_per_side=20, close_mode="two_level"),
                    )
                },
            ),
            patch.object(tick_shadow, "engine_from_args", side_effect=fake_engine_from_args),
            patch.object(tick_shadow.mt5, "symbol_info", return_value=object()),
            patch.object(tick_shadow, "pip_size_for", return_value=0.0001),
        ):
            engines = tick_shadow.build_engines(
                symbols={"EURUSD"},
                raw_close_alpha=0.5,
                raw_close_style="all_profitable",
                raw_rearm_variant="rearm_lvl2_exc1",
                raw_rearm_cooldown_bars=0,
                raw_rearm_momentum_gate=True,
                raw_sell_gap=1,
                raw_buy_gap=1,
                raw_step_buy=0.0004,
                raw_step_sell=0.0002,
                raw_max_floating_loss_usd=-15.0,
                raw_max_lattice_window_bars=240,
                raw_breakout_buffer_pips=0.0,
                raw_cluster_aware_escape=False,
                raw_cluster_fill_tolerance=None,
                raw_guard_open_admission=False,
                raw_suppress_additional_levels_after_burst=False,
                raw_burst_open_threshold=2,
                raw_max_entry_spread_ratio=0.3,
                raw_liquidity_gap_spread_multiplier=2.5,
                raw_liquidity_gap_spread_lookback=60,
                raw_liquidity_gap_spread_floor_ratio=1.0,
                raw_adaptive_overlay_autopilot=True,
                raw_proven_step_ceiling=0.0003,
                raw_proven_step_buy_ceiling=0.0004,
                raw_proven_step_sell_ceiling=0.0002,
                min_positive_close_profit_usd=0.25,
                positive_only_closes=False,
                raw_symbol_overrides=None,
                bounded_rearm_variant=None,
                bounded_close_gap=1,
                bounded_same_bar_min_pnl=0.0,
                bounded_same_bar_shallow_level_cap=0,
                bounded_timeframe="M1",
                bounded_step_pips=None,
                bounded_max_open_per_side=None,
                bounded_max_floating_loss_usd=None,
                bounded_vwap_lookback=None,
                bounded_regime_lookback_bars=None,
                bounded_max_range_pips=None,
                bounded_breakout_buffer_pips=None,
                bounded_max_lattice_window_bars=None,
                bounded_cooldown_bars=None,
            )

        self.assertIn("EURUSD", engines)
        self.assertEqual(captured["symbol"], "EURUSD")
        self.assertEqual(captured["close_alpha"], 0.5)
        self.assertEqual(captured["variant_name"], "rearm_lvl2_exc1")
        self.assertTrue(captured["momentum_gate"])
        self.assertAlmostEqual(float(captured["step_buy"]), 0.0004)
        self.assertAlmostEqual(float(captured["step_sell"]), 0.0002)
        self.assertAlmostEqual(float(captured["cluster_fill_tolerance"]), 0.0001)
        self.assertAlmostEqual(float(captured["max_entry_spread_ratio"]), 0.3)
        self.assertAlmostEqual(float(captured["liquidity_gap_spread_multiplier"]), 2.5)
        self.assertEqual(int(captured["liquidity_gap_spread_lookback"]), 60)
        self.assertAlmostEqual(float(captured["liquidity_gap_spread_floor_ratio"]), 1.0)
        self.assertTrue(captured["adaptive_overlay_autopilot"])
        self.assertFalse(bool(captured["offensive_closure_enabled"]))
        self.assertAlmostEqual(float(captured["offensive_safety_margin_usd"]), 0.0)
        self.assertAlmostEqual(float(captured["offensive_safety_margin_pct"]), 0.0)
        self.assertEqual(int(captured["offensive_cut_cooldown_bars"]), 0)
        self.assertAlmostEqual(float(captured["offensive_breakeven_band_usd"]), 0.0)
        self.assertAlmostEqual(float(captured["offensive_budget_share"]), 0.0)
        self.assertAlmostEqual(float(captured["proven_step_ceiling"]), 0.0003)
        self.assertAlmostEqual(float(captured["proven_step_buy_ceiling"]), 0.0004)
        self.assertAlmostEqual(float(captured["proven_step_sell_ceiling"]), 0.0002)
        self.assertAlmostEqual(float(captured["min_positive_close_profit_usd"]), 0.25)

    def test_build_engines_uses_symbol_override_max_entry_spread_ratio(self) -> None:
        captured: dict[str, object] = {}

        def fake_engine_from_args(**kwargs):
            captured.update(kwargs)
            return object()

        with (
            patch.object(
                tick_shadow,
                "default_apex_mix",
                return_value={
                    "NZDUSD": (
                        "raw_close2",
                        tick_shadow.RawConfig(step_pips=3.0, max_open_per_side=20, close_mode="two_level"),
                    )
                },
            ),
            patch.object(tick_shadow, "engine_from_args", side_effect=fake_engine_from_args),
            patch.object(tick_shadow.mt5, "symbol_info", return_value=object()),
            patch.object(tick_shadow, "pip_size_for", return_value=0.0001),
        ):
            engines = tick_shadow.build_engines(
                symbols={"NZDUSD"},
                raw_close_alpha=1.0,
                raw_close_style="all_profitable",
                raw_rearm_variant="rearm_lvl2_exc1",
                raw_rearm_cooldown_bars=12,
                raw_rearm_momentum_gate=True,
                raw_sell_gap=1,
                raw_buy_gap=1,
                raw_step_buy=0.0005,
                raw_step_sell=0.0003,
                raw_max_floating_loss_usd=-100.0,
                raw_max_lattice_window_bars=288,
                raw_breakout_buffer_pips=0.0,
                raw_cluster_aware_escape=False,
                raw_cluster_fill_tolerance=None,
                raw_guard_open_admission=False,
                raw_suppress_additional_levels_after_burst=False,
                raw_burst_open_threshold=2,
                raw_max_entry_spread_ratio=0.3,
                raw_liquidity_gap_spread_multiplier=2.5,
                raw_liquidity_gap_spread_lookback=60,
                raw_liquidity_gap_spread_floor_ratio=1.0,
                raw_adaptive_overlay_autopilot=True,
                raw_proven_step_ceiling=0.0003,
                raw_proven_step_buy_ceiling=0.0,
                raw_proven_step_sell_ceiling=0.0,
                min_positive_close_profit_usd=0.25,
                positive_only_closes=False,
                raw_symbol_overrides={
                    "NZDUSD": {
                        "max_entry_spread_ratio": 0.2,
                        "liquidity_gap_spread_multiplier": 3.0,
                        "liquidity_gap_spread_lookback": 90,
                        "liquidity_gap_spread_floor_ratio": 1.2,
                        "step_buy": 0.0004,
                        "step_sell": 0.0002,
                        "min_positive_close_profit_usd": 0.35,
                        "offensive_closure_enabled": True,
                        "offensive_safety_margin_usd": 2.0,
                        "offensive_safety_margin_pct": 0.2,
                        "offensive_cut_cooldown_bars": 5,
                        "offensive_breakeven_band_usd": 0.5,
                        "offensive_budget_share": 0.25,
                    }
                },
                bounded_rearm_variant=None,
                bounded_close_gap=1,
                bounded_same_bar_min_pnl=0.0,
                bounded_same_bar_shallow_level_cap=0,
                bounded_timeframe="M1",
                bounded_step_pips=None,
                bounded_max_open_per_side=None,
                bounded_max_floating_loss_usd=None,
                bounded_vwap_lookback=None,
                bounded_regime_lookback_bars=None,
                bounded_max_range_pips=None,
                bounded_breakout_buffer_pips=None,
                bounded_max_lattice_window_bars=None,
                bounded_cooldown_bars=None,
            )

        self.assertIn("NZDUSD", engines)
        self.assertAlmostEqual(float(captured["max_entry_spread_ratio"]), 0.2)
        self.assertAlmostEqual(float(captured["liquidity_gap_spread_multiplier"]), 3.0)
        self.assertEqual(int(captured["liquidity_gap_spread_lookback"]), 90)
        self.assertAlmostEqual(float(captured["liquidity_gap_spread_floor_ratio"]), 1.2)
        self.assertAlmostEqual(float(captured["step_buy"]), 0.0004)
        self.assertAlmostEqual(float(captured["step_sell"]), 0.0002)
        self.assertAlmostEqual(float(captured["min_positive_close_profit_usd"]), 0.35)
        self.assertTrue(bool(captured["offensive_closure_enabled"]))
        self.assertAlmostEqual(float(captured["offensive_safety_margin_usd"]), 2.0)
        self.assertAlmostEqual(float(captured["offensive_safety_margin_pct"]), 0.2)
        self.assertEqual(int(captured["offensive_cut_cooldown_bars"]), 5)
        self.assertAlmostEqual(float(captured["offensive_breakeven_band_usd"]), 0.5)
        self.assertAlmostEqual(float(captured["offensive_budget_share"]), 0.25)

    def test_build_engines_passes_bounded_overrides(self) -> None:
        captured: dict[str, object] = {}

        def fake_bounded_engine_from_args(**kwargs):
            captured.update(kwargs)
            return object()

        with (
            patch.object(
                tick_shadow,
                "default_apex_mix",
                return_value={
                    "USDJPY": (
                        "v3_bounded",
                        BoundedConfig(
                            step_pips=0.5,
                            max_open_per_side=20,
                            max_floating_loss_usd=-10.0,
                            vwap_lookback=20,
                            regime_lookback_bars=60,
                            max_range_pips=24.0,
                            breakout_buffer_pips=5.0,
                            max_lattice_window_bars=240,
                            cooldown_bars=60,
                        ),
                    )
                },
            ),
            patch.object(tick_shadow, "bounded_engine_from_args", side_effect=fake_bounded_engine_from_args),
        ):
            engines = tick_shadow.build_engines(
                symbols={"USDJPY"},
                raw_close_alpha=0.0,
                raw_close_style="all_profitable",
                raw_rearm_variant=None,
                raw_rearm_cooldown_bars=0,
                raw_rearm_momentum_gate=False,
                raw_sell_gap=None,
                raw_buy_gap=None,
                raw_step_buy=None,
                raw_step_sell=None,
                raw_max_floating_loss_usd=-15.0,
                raw_max_lattice_window_bars=240,
                raw_breakout_buffer_pips=0.0,
                raw_cluster_aware_escape=False,
                raw_cluster_fill_tolerance=None,
                raw_guard_open_admission=False,
                raw_suppress_additional_levels_after_burst=False,
                raw_burst_open_threshold=2,
                raw_max_entry_spread_ratio=0.3,
                raw_liquidity_gap_spread_multiplier=0.0,
                raw_liquidity_gap_spread_lookback=0,
                raw_liquidity_gap_spread_floor_ratio=0.0,
                raw_adaptive_overlay_autopilot=False,
                raw_proven_step_ceiling=0.0,
                raw_proven_step_buy_ceiling=0.0,
                raw_proven_step_sell_ceiling=0.0,
                min_positive_close_profit_usd=0.25,
                positive_only_closes=False,
                raw_symbol_overrides=None,
                bounded_rearm_variant="rearm_lvl2_exc2",
                bounded_close_gap=1,
                bounded_same_bar_min_pnl=0.03,
                bounded_same_bar_shallow_level_cap=2,
                bounded_timeframe="M5",
                bounded_step_pips=1.0,
                bounded_max_open_per_side=12,
                bounded_max_floating_loss_usd=-8.0,
                bounded_vwap_lookback=30,
                bounded_regime_lookback_bars=90,
                bounded_max_range_pips=18.0,
                bounded_breakout_buffer_pips=4.0,
                bounded_max_lattice_window_bars=180,
                bounded_cooldown_bars=45,
            )

        self.assertIn("USDJPY", engines)
        self.assertEqual(captured["timeframe_name"], "M5")
        self.assertEqual(captured["same_bar_min_pnl"], 0.03)
        self.assertEqual(captured["same_bar_shallow_level_cap"], 2)
        self.assertFalse(captured["cluster_aware_escape"])
        self.assertAlmostEqual(float(captured["cluster_fill_tolerance"]), 0.01)
        self.assertAlmostEqual(float(captured["max_entry_spread_ratio"]), 0.3)
        self.assertFalse(captured["adaptive_overlay_autopilot"])
        self.assertAlmostEqual(float(captured["min_positive_close_profit_usd"]), 0.25)
        cfg = captured["cfg"]
        self.assertIsInstance(cfg, BoundedConfig)
        self.assertEqual(cfg.step_pips, 1.0)
        self.assertEqual(cfg.max_open_per_side, 12)
        self.assertEqual(cfg.max_floating_loss_usd, -8.0)
        self.assertEqual(cfg.vwap_lookback, 30)
        self.assertEqual(cfg.regime_lookback_bars, 90)
        self.assertEqual(cfg.max_range_pips, 18.0)
        self.assertEqual(cfg.breakout_buffer_pips, 4.0)
        self.assertEqual(cfg.max_lattice_window_bars, 180)
        self.assertEqual(cfg.cooldown_bars, 45)


if __name__ == "__main__":
    unittest.main()
