#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hungry_hippo_rearm as rearm


class HungryHippoRearmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.surfaces = {
            "regime_rows": {
                "BTCUSD": {"symbol": "BTCUSD", "control_mode": "breakout_follow", "action_bias": "SELL"},
                "GBPUSD": {"symbol": "GBPUSD", "control_mode": "breakout_follow", "action_bias": "BUY"},
                "NZDUSD": {
                    "symbol": "NZDUSD",
                    "control_mode": "wait_extreme_confirmation",
                    "action_bias": "NEUTRAL",
                },
                "XRPUSD": {"symbol": "XRPUSD", "control_mode": "breakout_follow", "action_bias": "BUY"},
            },
            "session_windows": {
                "XAUUSD": {"window": "06:00-10:00+13:00-17:00", "off_hour_weight": 0.5},
            },
            "btc_hold_gate": {"deploy_decision": "hold_current_bullish_shape"},
        }

    def test_btc_sell_hold_blocks_regime_mismatch_rearm(self) -> None:
        row = rearm.compute_rearm_params("BTCUSD", "regime_mismatch", 0, 14, [2.0, 3.0, 1.5], surfaces=self.surfaces)
        self.assertEqual(row["canonical_guardrail_status"], "blocked")
        self.assertEqual(row["max_injections"], 0)
        self.assertFalse(row["should_rearm_now"])

    def test_wait_extreme_symbol_blocks_auto_rearm(self) -> None:
        row = rearm.compute_rearm_params("NZDUSD", "manual_kill", 0, 7, [2.0, 3.0, 1.5], surfaces=self.surfaces)
        self.assertEqual(row["canonical_guardrail_status"], "blocked")
        self.assertEqual(row["guardrail_control_mode"], "wait_extreme_confirmation")
        self.assertEqual(row["max_injections"], 0)
        self.assertFalse(row["should_rearm_now"])

    def test_xau_uses_canonical_session_window(self) -> None:
        row = rearm.compute_rearm_params("XAUUSD", "manual_kill", 0, 23, [2.0, 3.0, 1.5], surfaces=self.surfaces)
        self.assertEqual(row["session_window_source"], "canonical_session_table")
        self.assertEqual(row["session_window"], "06:00-10:00+13:00-17:00")
        self.assertFalse(row["is_active_hour"])

    def test_gbpusd_trend_follow_stays_enabled_during_active_hour(self) -> None:
        row = rearm.compute_rearm_params("GBPUSD", "manual_kill", 0, 14, [2.0, 3.0, 1.5], surfaces=self.surfaces)
        self.assertEqual(row["canonical_guardrail_status"], "aligned")
        self.assertEqual(row["max_injections"], 2)
        self.assertTrue(row["should_rearm_now"])

    def test_xrpusd_seeded_policy_surfaces_enable_rearm_contract(self) -> None:
        row = rearm.compute_rearm_params("XRPUSD", "manual_kill", 0, 15, [2.0, 3.0, 1.5], surfaces=self.surfaces)
        self.assertEqual(row["canonical_guardrail_status"], "aligned")
        self.assertEqual(row["guardrail_control_mode"], "breakout_follow")
        self.assertEqual(row["max_injections"], 2)
        self.assertTrue(row["should_rearm_now"])


if __name__ == "__main__":
    unittest.main()
