#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import launch_fx_m5_float_zero_shadow as launcher


class LaunchFxM5FloatZeroShadowTests(unittest.TestCase):
    def test_build_lane_contract_matches_gbp_winner(self) -> None:
        lane = launcher.build_lane_contract("GBPUSD")
        args = lane["restart_args"]

        self.assertEqual(lane["name"], "shadow_gbpusd_m5_snake_float_zero_v1")
        self.assertEqual(lane["kind"], "shadow_fx")
        self.assertEqual(lane["engine_family"], "snake_counter_web_shadow")
        self.assertEqual(args[args.index("--symbol") + 1], "GBPUSD")
        self.assertEqual(args[args.index("--timeframe") + 1], "M5")
        self.assertEqual(args[args.index("--step-pips") + 1], "0.5")
        self.assertEqual(args[args.index("--retrace-steps") + 1], "5")
        self.assertEqual(args[args.index("--hold-frontier") + 1], "0")
        self.assertEqual(args[args.index("--controller-mode") + 1], "static")
        self.assertEqual(args[args.index("--portfolio-close-mode") + 1], "float_zero")
        self.assertIn("--rebase-on-flat", args)
        self.assertNotIn("--session-gate", args)
        self.assertEqual(lane["contract_meta"]["winner_booked_usd_per_hour"], 4.316)

    def test_build_lane_contract_matches_eur_winner(self) -> None:
        lane = launcher.build_lane_contract("EURUSD")
        args = lane["restart_args"]

        self.assertEqual(lane["name"], "shadow_eurusd_m5_snake_float_zero_v1")
        self.assertEqual(args[args.index("--symbol") + 1], "EURUSD")
        self.assertEqual(args[args.index("--retrace-steps") + 1], "6")
        self.assertEqual(
            args[args.index("--variant-label") + 1],
            "snake_step0.5pip_retrace6_hold0_static_float_zero_cap64_rebase",
        )
        self.assertEqual(lane["contract_meta"]["winner_booked_usd_per_hour"], 2.632)

    def test_upsert_registry_lane_enables_row_and_strips_contract_meta(self) -> None:
        registry = {"lanes": []}
        lane = launcher.build_lane_contract("GBPUSD")
        changed = launcher.upsert_registry_lane(registry, lane)
        self.assertTrue(changed)
        row = registry["lanes"][0]
        self.assertTrue(row["enabled"])
        self.assertEqual(row["pause_note"], "")
        self.assertNotIn("contract_meta", row)

    def test_ensure_watchdog_membership_updates_shadow_lists_once(self) -> None:
        watchdog = {
            "groups": {"shadow_watchdog": {"lanes": ["shadow_coinbase_btc_perp"]}},
        }
        changed_first = launcher.ensure_watchdog_membership(watchdog, "shadow_gbpusd_m5_snake_float_zero_v1")
        changed_second = launcher.ensure_watchdog_membership(watchdog, "shadow_gbpusd_m5_snake_float_zero_v1")
        self.assertTrue(changed_first)
        self.assertFalse(changed_second)
        self.assertIn("shadow_gbpusd_m5_snake_float_zero_v1", watchdog["groups"]["shadow_watchdog"]["lanes"])
        self.assertIn("shadow_gbpusd_m5_snake_float_zero_v1", watchdog["shadow_watchdog"]["lanes"])


if __name__ == "__main__":
    unittest.main()
