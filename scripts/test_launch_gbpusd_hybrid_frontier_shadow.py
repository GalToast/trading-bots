#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import launch_gbpusd_hybrid_frontier_shadow as launcher


class LaunchGBPUSDHybridFrontierShadowTests(unittest.TestCase):
    def test_build_lane_contract_matches_offline_winner(self) -> None:
        lane = launcher.build_lane_contract()
        args = lane["restart_args"]

        self.assertEqual(lane["name"], launcher.LANE_NAME)
        self.assertEqual(lane["kind"], "shadow_fx")
        self.assertEqual(lane["state_path"], launcher.STATE_PATH)
        self.assertEqual(lane["event_path"], launcher.EVENT_PATH)
        self.assertEqual(args[args.index("--symbol") + 1], "GBPUSD")
        self.assertEqual(args[args.index("--timeframe") + 1], "M15")
        self.assertEqual(args[args.index("--step-buy") + 1], launcher.STEP_BUY)
        self.assertEqual(args[args.index("--step-sell") + 1], launcher.STEP_SELL)
        self.assertEqual(args[args.index("--max-open-per-side") + 1], launcher.MAX_OPEN_PER_SIDE)
        self.assertEqual(args[args.index("--raw-close-alpha") + 1], "1.0")
        self.assertEqual(args[args.index("--raw-close-style") + 1], "harvest_inner_hold_frontier")
        self.assertEqual(args[args.index("--raw-sell-gap") + 1], "1")
        self.assertEqual(args[args.index("--raw-buy-gap") + 1], "2")
        self.assertEqual(args[args.index("--max-floating-loss-usd") + 1], launcher.MAX_FLOATING_LOSS_USD)
        self.assertEqual(args[args.index("--max-entry-spread-ratio") + 1], launcher.MAX_ENTRY_SPREAD_RATIO)
        self.assertIn("--session-gate", args)
        self.assertIn("--adaptive-overlay-autopilot", args)
        self.assertNotIn("--fresh-start", args)
        self.assertEqual(lane["contract_meta"]["study_variant_label"], "harvest_inner_hold_frontier_step0.75_cap+3")
        self.assertTrue(lane["contract_meta"]["session_gate"])

    def test_upsert_registry_lane_enables_row_and_strips_contract_meta(self) -> None:
        registry = {"lanes": []}
        lane = launcher.build_lane_contract()
        changed = launcher.upsert_registry_lane(registry, lane)
        self.assertTrue(changed)
        row = registry["lanes"][0]
        self.assertTrue(row["enabled"])
        self.assertEqual(row["pause_note"], "")
        self.assertNotIn("contract_meta", row)

    def test_ensure_watchdog_membership_updates_both_crypto_lists_once(self) -> None:
        watchdog = {
            "groups": {"crypto_watchdog": {"lanes": ["shadow_ethusd_m5_structure_shapeshifter"]}},
            "crypto_watchdog": {"lanes": ["shadow_ethusd_m5_structure_shapeshifter"]},
        }
        changed_first = launcher.ensure_watchdog_membership(watchdog, launcher.LANE_NAME)
        changed_second = launcher.ensure_watchdog_membership(watchdog, launcher.LANE_NAME)
        self.assertTrue(changed_first)
        self.assertFalse(changed_second)
        self.assertIn(launcher.LANE_NAME, watchdog["groups"]["crypto_watchdog"]["lanes"])
        self.assertIn(launcher.LANE_NAME, watchdog["crypto_watchdog"]["lanes"])


if __name__ == "__main__":
    unittest.main()
