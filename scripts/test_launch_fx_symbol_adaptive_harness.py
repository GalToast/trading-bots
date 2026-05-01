#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import launch_fx_symbol_adaptive_harness as harness


class LaunchFXSymbolAdaptiveHarnessTests(unittest.TestCase):
    def test_build_lane_contract_eurusd_prefers_symbol_tuning_over_shape_defaults(self) -> None:
        lane = harness.build_lane_contract("EURUSD")
        self.assertEqual(lane["name"], f"live_eurusd_adaptive_harness_{harness.PINNED_LIVE_MAGICS['EURUSD']}")
        self.assertEqual(lane["kind"], "live_fx")
        self.assertEqual(lane["engine_family"], "raw")
        self.assertEqual(lane["state_path"], "reports/penetration_lattice_live_eurusd_adaptive_harness_state.json")
        args = lane["restart_args"]
        self.assertIn("--direct-live", args)
        self.assertIn("--session-gate", args)
        self.assertIn("--adaptive-overlay-autopilot", args)
        self.assertIn("--raw-rearm-variant", args)
        self.assertIn("rearm_lvl2_exc1", args)
        self.assertIn("--raw-close-style", args)
        self.assertEqual(args[args.index("--raw-close-style") + 1], "all_profitable")
        self.assertIn("--raw-sell-gap", args)
        self.assertIn("--raw-buy-gap", args)
        self.assertEqual(args[args.index("--raw-sell-gap") + 1], "1")
        self.assertEqual(args[args.index("--raw-buy-gap") + 1], "1")
        self.assertEqual(args[args.index("--step-buy") + 1], "0.00092")
        self.assertEqual(args[args.index("--step-sell") + 1], "0.00092")
        self.assertIn("--max-entry-spread-ratio", args)
        self.assertIn("0.3", args)
        self.assertIn("--min-positive-close-profit-usd", args)
        self.assertEqual(args[args.index("--min-positive-close-profit-usd") + 1], "0.25")
        self.assertIn("--positive-only-closes", args)
        self.assertIn("--proven-step-buy-ceiling", args)
        self.assertEqual(args[args.index("--proven-step-buy-ceiling") + 1], "0.00092")
        self.assertIn("--proven-step-sell-ceiling", args)
        self.assertEqual(args[args.index("--proven-step-sell-ceiling") + 1], "0.00092")
        self.assertEqual(lane["contract_meta"]["adaptive_shape_id"], "eurusd_mixed_floor_v1")
        self.assertEqual(lane["contract_meta"]["raw_sell_gap"], 1)
        self.assertEqual(lane["contract_meta"]["raw_buy_gap"], 1)
        self.assertEqual(lane["contract_meta"]["raw_close_style"], "all_profitable")
        self.assertEqual(lane["contract_meta"]["raw_rearm_variant"], "rearm_lvl2_exc1")
        self.assertEqual(lane["contract_meta"]["min_positive_close_profit_usd"], 0.25)
        self.assertTrue(lane["contract_meta"]["positive_only_closes"])
        self.assertEqual(lane["contract_meta"]["step_buy_price_units"], 0.00092)
        self.assertEqual(lane["contract_meta"]["step_sell_price_units"], 0.00092)

    def test_build_lane_contract_nzdusd_loosens_spread_gate_and_keeps_momentum_gate(self) -> None:
        lane = harness.build_lane_contract("NZDUSD")
        args = lane["restart_args"]
        self.assertEqual(lane["name"], f"live_nzdusd_adaptive_harness_{harness.PINNED_LIVE_MAGICS['NZDUSD']}")
        self.assertIn("--raw-rearm-momentum-gate", args)
        self.assertEqual(args[args.index("--raw-close-alpha") + 1], "0.5")
        self.assertEqual(args[args.index("--raw-sell-gap") + 1], "1")
        self.assertEqual(args[args.index("--raw-buy-gap") + 1], "1")
        self.assertEqual(args[args.index("--step-buy") + 1], "0.0004")
        self.assertEqual(args[args.index("--step-sell") + 1], "0.0002")
        self.assertEqual(args[args.index("--raw-rearm-variant") + 1], "rearm_lvl2_exc1")
        ratio_index = args.index("--max-entry-spread-ratio") + 1
        self.assertEqual(args[ratio_index], "0.35")
        self.assertEqual(args[args.index("--min-positive-close-profit-usd") + 1], "0.25")
        self.assertIn("--positive-only-closes", args)
        self.assertEqual(args[args.index("--proven-step-buy-ceiling") + 1], "0.0004")
        self.assertEqual(args[args.index("--proven-step-sell-ceiling") + 1], "0.0002")
        self.assertEqual(lane["contract_meta"]["adaptive_shape_id"], "nzdusd_asym_probe_v1")
        self.assertEqual(lane["contract_meta"]["min_positive_close_profit_usd"], 0.25)
        self.assertTrue(lane["contract_meta"]["positive_only_closes"])
        self.assertEqual(lane["contract_meta"]["step_buy_price_units"], 0.0004)
        self.assertEqual(lane["contract_meta"]["step_sell_price_units"], 0.0002)

    def test_build_lane_contract_usdjpy_uses_bounded_live_shape(self) -> None:
        lane = harness.build_lane_contract("USDJPY")
        args = lane["restart_args"]
        self.assertEqual(lane["name"], f"live_usdjpy_adaptive_harness_{harness.PINNED_LIVE_MAGICS['USDJPY']}")
        self.assertEqual(lane["engine_family"], "bounded")
        self.assertIn("--bounded-rearm-variant", args)
        self.assertIn("rearm_lvl2_exc2", args)
        self.assertIn("--bounded-close-gap", args)
        self.assertIn("2", args)
        self.assertEqual(args[args.index("--max-entry-spread-ratio") + 1], "1.2")
        self.assertEqual(args[args.index("--min-positive-close-profit-usd") + 1], "0.25")
        self.assertIn("--positive-only-closes", args)
        self.assertIn("--session-gate", args)
        self.assertIn("--direct-live", args)
        self.assertEqual(lane["contract_meta"]["min_positive_close_profit_usd"], 0.25)
        self.assertTrue(lane["contract_meta"]["positive_only_closes"])

    def test_build_lane_contract_gbpusd_uses_adaptive_shape_close_contract(self) -> None:
        lane = harness.build_lane_contract("GBPUSD")
        args = lane["restart_args"]

        self.assertEqual(lane["name"], f"live_gbpusd_adaptive_harness_{harness.PINNED_LIVE_MAGICS['GBPUSD']}")
        self.assertEqual(lane["contract_meta"]["adaptive_shape_id"], "gbpusd_trend_harvest_v1")
        self.assertEqual(args[args.index("--raw-close-alpha") + 1], "0.5")
        self.assertEqual(args[args.index("--raw-close-style") + 1], "all_profitable")
        self.assertEqual(args[args.index("--raw-rearm-variant") + 1], "rearm_lvl2_exc1")
        self.assertEqual(args[args.index("--raw-rearm-cooldown-bars") + 1], "0")
        self.assertEqual(args[args.index("--raw-sell-gap") + 1], "1")
        self.assertEqual(args[args.index("--raw-buy-gap") + 1], "3")
        self.assertEqual(args[args.index("--min-positive-close-profit-usd") + 1], "0.25")
        self.assertIn("--positive-only-closes", args)
        self.assertEqual(args[args.index("--step-buy") + 1], "0.0011")
        self.assertEqual(args[args.index("--step-sell") + 1], "0.00055")
        self.assertEqual(args[args.index("--proven-step-buy-ceiling") + 1], "0.0011")
        self.assertEqual(args[args.index("--proven-step-sell-ceiling") + 1], "0.00055")
        self.assertEqual(lane["contract_meta"]["min_positive_close_profit_usd"], 0.25)
        self.assertTrue(lane["contract_meta"]["positive_only_closes"])
        self.assertEqual(lane["contract_meta"]["step_buy_price_units"], 0.0011)
        self.assertEqual(lane["contract_meta"]["step_sell_price_units"], 0.00055)

    def test_upsert_registry_lane_sets_pause_note_for_disabled_cutover_rows(self) -> None:
        registry = {"lanes": []}
        lane = harness.build_lane_contract("GBPUSD")
        changed = harness.upsert_registry_lane(registry, lane, enabled=False)
        self.assertTrue(changed)
        self.assertEqual(len(registry["lanes"]), 1)
        row = registry["lanes"][0]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["pause_note"], "awaiting_cutover_from_shared_fx_seats")
        self.assertNotIn("contract_meta", row)

    def test_ensure_watchdog_membership_adds_lane_once(self) -> None:
        watchdog = {"groups": {"fx_watchdog": {"lanes": ["live_rearm_941777"]}}}
        changed_first = harness.ensure_watchdog_membership(watchdog, "live_eurusd_adaptive_harness_941885")
        changed_second = harness.ensure_watchdog_membership(watchdog, "live_eurusd_adaptive_harness_941885")
        self.assertTrue(changed_first)
        self.assertFalse(changed_second)
        self.assertIn("live_eurusd_adaptive_harness_941885", watchdog["groups"]["fx_watchdog"]["lanes"])
        self.assertIn("live_eurusd_adaptive_harness_941885", watchdog["fx_watchdog"]["lanes"])

    def test_build_lane_contract_supports_single_symbol_magic_override(self) -> None:
        lane = harness.build_lane_contract("GBPUSD", live_magic=951777)
        args = lane["restart_args"]
        self.assertEqual(lane["name"], "live_gbpusd_adaptive_harness_951777")
        self.assertEqual(lane["state_path"], "reports/penetration_lattice_live_gbpusd_adaptive_harness_951777_state.json")
        self.assertEqual(lane["event_path"], "reports/penetration_lattice_live_gbpusd_adaptive_harness_951777_events.jsonl")
        self.assertEqual(args[args.index("--state-path") + 1], "reports/penetration_lattice_live_gbpusd_adaptive_harness_951777_state.json")
        self.assertEqual(args[args.index("--event-path") + 1], "reports/penetration_lattice_live_gbpusd_adaptive_harness_951777_events.jsonl")
        self.assertEqual(args[args.index("--live-magic") + 1], "951777")
        self.assertEqual(lane["contract_meta"]["live_magic"], 951777)

    def test_deactivate_other_family_rows_pauses_superseded_contracts(self) -> None:
        registry = {
            "lanes": [
                {"name": "live_gbpusd_adaptive_harness_941777", "enabled": True, "pause_note": ""},
                {"name": "live_gbpusd_adaptive_harness_951777", "enabled": True, "pause_note": ""},
                {"name": "live_eurusd_adaptive_harness_941885", "enabled": True, "pause_note": ""},
            ]
        }
        watchdog = {
            "groups": {
                "fx_watchdog": {
                    "lanes": [
                        "live_gbpusd_adaptive_harness_941777",
                        "live_gbpusd_adaptive_harness_951777",
                        "live_eurusd_adaptive_harness_941885",
                    ]
                }
            },
            "fx_watchdog": {
                "lanes": [
                    "live_gbpusd_adaptive_harness_941777",
                    "live_gbpusd_adaptive_harness_951777",
                    "live_eurusd_adaptive_harness_941885",
                ]
            },
        }
        changed = harness.deactivate_other_family_rows(
            registry,
            watchdog,
            family_prefix="live_gbpusd_adaptive_harness_",
            keep_lane_name="live_gbpusd_adaptive_harness_951777",
        )
        self.assertTrue(changed)
        old_row = registry["lanes"][0]
        kept_row = registry["lanes"][1]
        self.assertFalse(old_row["enabled"])
        self.assertEqual(old_row["pause_note"], "superseded_by_live_gbpusd_adaptive_harness_951777")
        self.assertTrue(kept_row["enabled"])
        self.assertEqual(
            watchdog["groups"]["fx_watchdog"]["lanes"],
            ["live_gbpusd_adaptive_harness_951777", "live_eurusd_adaptive_harness_941885"],
        )
        self.assertEqual(
            watchdog["fx_watchdog"]["lanes"],
            ["live_gbpusd_adaptive_harness_951777", "live_eurusd_adaptive_harness_941885"],
        )

    def test_cutover_requires_superseded_family_rows_to_be_broker_flat(self) -> None:
        registry = {
            "lanes": [
                {
                    "name": "live_gbpusd_adaptive_harness_941777",
                    "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py", "--live-magic", "941777"],
                },
                {
                    "name": "live_gbpusd_adaptive_harness_951777",
                    "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py", "--live-magic", "951777"],
                },
            ]
        }
        with (
            patch.object(harness.mt5_terminal_guard, "initialize_mt5", return_value=(True, {"reason": "ok"})),
            patch.object(harness.live_mirror, "broker_live_positions", return_value=[{"ticket": 1}]),
            patch.object(harness.mt5, "shutdown"),
        ):
            with self.assertRaisesRegex(RuntimeError, "still has 1 broker positions"):
                harness.ensure_cutover_rows_are_broker_flat(
                    registry,
                    family_prefix="live_gbpusd_adaptive_harness_",
                    keep_lane_name="live_gbpusd_adaptive_harness_951777",
                )


if __name__ == "__main__":
    unittest.main()
