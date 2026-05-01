#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_joint_control_law_study as study


class JointControlLawStudyTests(unittest.TestCase):
    def test_anchor_confidence_drops_on_persistent_runaway(self) -> None:
        bars = []
        price = 100.0
        for i in range(24):
            bars.append(
                {
                    "time": i,
                    "open": price,
                    "high": price + 2.0,
                    "low": price - 0.2,
                    "close": price + 1.5,
                    "tick_volume": 100,
                }
            )
            price += 1.5
        state = study.compute_anchor_state(bars=bars, idx=len(bars) - 1, anchor=100.0, avg_step_px=1.0)
        self.assertLess(state.confidence, 0.35)
        self.assertGreater(state.distance_steps, 5.0)
        self.assertGreater(state.trend_persistence, 0.8)

    def test_anchor_confidence_stays_higher_in_balanced_reclaiming_path(self) -> None:
        closes = [100.0, 101.0, 100.4, 100.8, 100.2, 100.6, 100.1, 100.4, 100.0, 100.2]
        bars = [
            {
                "time": i,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "tick_volume": 100,
            }
            for i, close in enumerate(closes)
        ]
        state = study.compute_anchor_state(bars=bars, idx=len(bars) - 1, anchor=100.0, avg_step_px=1.0)
        self.assertGreater(state.confidence, 0.55)
        self.assertLess(state.distance_steps, 1.0)

    def test_adaptive_target_signed_level_uses_depth_confidence_and_time(self) -> None:
        ticket = study.TicketState(direction="SELL", entry_price=105.0, opened_time=1, opened_idx=1, level_idx=6)
        contract = study.JointContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="shape",
            step_buy_px=1.0,
            step_sell_px=1.0,
            max_open_per_side=10,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            base_variant_label="base",
            geometry_label="step1.00_cap10",
            control_name="depth_split_reclaim",
            control_mode="adaptive_depth",
            control_description="",
            close_style="adaptive_depth",
            close_alpha=0.0,
            sell_gap=2,
            buy_gap=2,
            open_gate_low_confidence=True,
            min_gate_distance_steps=4.0,
            skip_rearm_below_confidence=0.35,
            hybrid_profile="depth_split_reclaim",
            time_soft_limit_bars=24,
        )
        high_conf = study.AnchorState(confidence=0.8, distance_steps=6.0, trend_persistence=0.2, range_expansion=1.0, moved_toward_anchor=True)
        low_conf = study.AnchorState(confidence=0.3, distance_steps=6.0, trend_persistence=0.9, range_expansion=1.8, moved_toward_anchor=False)
        self.assertEqual(study._adaptive_target_signed_level(ticket, contract, high_conf, hold_bars=6), -1)
        self.assertEqual(study._adaptive_target_signed_level(ticket, contract, low_conf, hold_bars=6), 0)
        self.assertEqual(study._adaptive_target_signed_level(ticket, contract, high_conf, hold_bars=30), 5)

    def test_low_confidence_open_gate_blocks_distant_supply(self) -> None:
        contract = study.JointContract(
            symbol="EURUSD",
            timeframe="M15",
            shape_id="shape",
            step_buy_px=1.0,
            step_sell_px=1.0,
            max_open_per_side=10,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            base_variant_label="base",
            geometry_label="step1.00_cap10",
            control_name="depth_split_cash_guard",
            control_mode="adaptive_depth",
            control_description="",
            close_style="adaptive_depth",
            close_alpha=0.0,
            sell_gap=2,
            buy_gap=2,
            open_gate_low_confidence=True,
            min_gate_distance_steps=3.0,
            skip_rearm_below_confidence=0.45,
            hybrid_profile="depth_split_cash_guard",
            time_soft_limit_bars=18,
        )
        low_conf = study.AnchorState(confidence=0.2, distance_steps=5.0, trend_persistence=0.9, range_expansion=1.6, moved_toward_anchor=False)
        near_anchor = study.AnchorState(confidence=0.2, distance_steps=1.5, trend_persistence=0.9, range_expansion=1.6, moved_toward_anchor=False)
        self.assertFalse(study._allow_new_opens(contract, low_conf))
        self.assertTrue(study._allow_new_opens(contract, near_anchor))


if __name__ == "__main__":
    unittest.main()
