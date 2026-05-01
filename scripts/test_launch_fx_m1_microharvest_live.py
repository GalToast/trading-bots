#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import launch_fx_m1_microharvest_live as launcher


class LaunchFxM1MicroharvestLiveTests(unittest.TestCase):
    def test_build_lane_contract_matches_gbp_margin_surviving_winner(self) -> None:
        lane = launcher.build_lane_contract("GBPUSD")
        args = lane["restart_args"]

        self.assertEqual(lane["name"], "live_gbpusd_m1_snake_microharvest_941795")
        self.assertEqual(lane["kind"], "live_fx")
        self.assertEqual(lane["engine_family"], "snake_counter_web_live")
        self.assertIn("--direct-live", args)
        self.assertEqual(args[args.index("--symbol") + 1], "GBPUSD")
        self.assertEqual(args[args.index("--timeframe") + 1], "M1")
        self.assertEqual(args[args.index("--step-pips") + 1], "0.1")
        self.assertEqual(args[args.index("--max-open-per-side") + 1], "16")
        self.assertEqual(args[args.index("--portfolio-close-mode") + 1], "float_zero")
        self.assertEqual(args[args.index("--hedge-mode") + 1], "same_level")
        self.assertEqual(args[args.index("--hedge-trigger-depth") + 1], "4")
        self.assertEqual(args[args.index("--min-harvest-profit-usd") + 1], "0.35")
        self.assertEqual(args[args.index("--max-entry-spread-ratio") + 1], "12.00")
        self.assertIn("--require-live-admissibility", args)
        self.assertIn("--positive-only-closes", args)
        self.assertIn("--block-on-prestart-open-carry", args)
        self.assertEqual(args[args.index("--live-magic") + 1], "941795")
        self.assertEqual(
            args[args.index("--variant-label") + 1],
            "snake_step0.1pip_retrace1_hold0_static_float_zero_hedgesame_level_cap16_rebase",
        )
        self.assertEqual(lane["contract_meta"]["winner_booked_usd_per_hour"], 3.376)
        self.assertEqual(lane["contract_meta"]["min_harvest_profit_usd"], 0.35)
        self.assertTrue(lane["contract_meta"]["require_live_admissibility"])
        self.assertTrue(lane["contract_meta"]["positive_only_closes"])
        self.assertTrue(lane["contract_meta"]["block_on_prestart_open_carry"])
        self.assertTrue(lane["contract_meta"]["rebase_on_flat"])

    def test_build_lane_contract_matches_eur_margin_surviving_winner(self) -> None:
        lane = launcher.build_lane_contract("EURUSD")
        args = lane["restart_args"]

        self.assertEqual(lane["name"], "live_eurusd_m1_snake_microharvest_941796")
        self.assertEqual(args[args.index("--symbol") + 1], "EURUSD")
        self.assertEqual(args[args.index("--live-magic") + 1], "941796")
        self.assertEqual(args[args.index("--step-pips") + 1], "0.1")
        self.assertEqual(args[args.index("--max-open-per-side") + 1], "16")
        self.assertEqual(
            args[args.index("--variant-label") + 1],
            "snake_step0.1pip_retrace1_hold0_static_float_zero_hedgedepth_threshold4_cap16_rebase",
        )
        self.assertEqual(args[args.index("--portfolio-close-mode") + 1], "float_zero")
        self.assertEqual(lane["contract_meta"]["hedge_mode"], "depth_threshold")
        self.assertEqual(lane["contract_meta"]["hedge_trigger_depth"], 4)
        self.assertEqual(args[args.index("--min-harvest-profit-usd") + 1], "0.20")
        self.assertEqual(args[args.index("--max-entry-spread-ratio") + 1], "14.00")
        self.assertIn("--require-live-admissibility", args)
        self.assertIn("--positive-only-closes", args)
        self.assertIn("--block-on-prestart-open-carry", args)
        self.assertEqual(lane["contract_meta"]["min_harvest_profit_usd"], 0.20)
        self.assertEqual(lane["contract_meta"]["max_entry_spread_ratio"], 14.0)
        self.assertEqual(lane["contract_meta"]["winner_booked_usd_per_hour"], 1.025)
        self.assertTrue(lane["contract_meta"]["positive_only_closes"])
        self.assertTrue(lane["contract_meta"]["block_on_prestart_open_carry"])
        self.assertTrue(lane["contract_meta"]["rebase_on_flat"])

    def test_upsert_registry_lane_enables_row_and_strips_contract_meta(self) -> None:
        registry = {"lanes": []}
        lane = launcher.build_lane_contract("GBPUSD")
        changed = launcher.upsert_registry_lane(registry, lane)
        self.assertTrue(changed)
        row = registry["lanes"][0]
        self.assertTrue(row["enabled"])
        self.assertEqual(row["pause_note"], "")
        self.assertNotIn("contract_meta", row)

    def test_ensure_watchdog_membership_updates_fx_lists_once(self) -> None:
        watchdog = {
            "groups": {"fx_watchdog": {"lanes": ["live_rearm_941777"]}},
        }
        changed_first = launcher.ensure_watchdog_membership(watchdog, "live_gbpusd_m1_snake_microharvest_941795")
        changed_second = launcher.ensure_watchdog_membership(watchdog, "live_gbpusd_m1_snake_microharvest_941795")
        self.assertTrue(changed_first)
        self.assertFalse(changed_second)
        self.assertIn("live_gbpusd_m1_snake_microharvest_941795", watchdog["groups"]["fx_watchdog"]["lanes"])
        self.assertIn("live_gbpusd_m1_snake_microharvest_941795", watchdog["fx_watchdog"]["lanes"])

    def test_launch_lane_reuses_alive_pid_without_recycle(self) -> None:
        lane = launcher.build_lane_contract("GBPUSD")
        with patch.object(launcher, "find_running_pid", return_value=12345):
            started, pid, recycled_from_pid = launcher.launch_lane(lane, recycle=False)
        self.assertFalse(started)
        self.assertEqual(pid, 12345)
        self.assertIsNone(recycled_from_pid)

    def test_launch_lane_recycles_existing_process(self) -> None:
        lane = launcher.build_lane_contract("GBPUSD")

        class Proc:
            pid = 22222

        with (
            patch.object(launcher, "find_running_pid", return_value=11111),
            patch.object(launcher, "backup_state_file", return_value=Path("backup.json")),
            patch.object(launcher, "terminate_process") as terminate_process,
            patch.object(launcher.subprocess, "Popen", return_value=Proc()) as popen,
        ):
            started, pid, recycled_from_pid = launcher.launch_lane(lane, recycle=True)

        self.assertTrue(started)
        self.assertEqual(pid, 22222)
        self.assertEqual(recycled_from_pid, 11111)
        terminate_process.assert_called_once_with(11111)
        popen.assert_called_once()

    def test_launch_lane_appends_fresh_start_only_to_launch_command(self) -> None:
        lane = launcher.build_lane_contract("GBPUSD")

        class Proc:
            pid = 33333

        with (
            patch.object(launcher, "find_running_pid", return_value=None),
            patch.object(launcher.subprocess, "Popen", return_value=Proc()) as popen,
        ):
            started, pid, recycled_from_pid = launcher.launch_lane(lane, fresh_start=True)

        self.assertTrue(started)
        self.assertEqual(pid, 33333)
        self.assertIsNone(recycled_from_pid)
        command = popen.call_args.args[0]
        self.assertEqual(command[-1], "--fresh-start")
        self.assertNotIn("--fresh-start", lane["restart_args"])

    def test_build_lane_contract_omits_rebase_flag_when_spec_disables_it(self) -> None:
        with patch.dict(launcher.LANE_SPECS["GBPUSD"], {"rebase_on_flat": False}, clear=False):
            lane = launcher.build_lane_contract("GBPUSD")

        self.assertNotIn("--rebase-on-flat", lane["restart_args"])
        self.assertFalse(lane["contract_meta"]["rebase_on_flat"])

    def test_build_lane_contract_supports_single_symbol_magic_override(self) -> None:
        lane = launcher.build_lane_contract("GBPUSD", live_magic=951795)
        self.assertEqual(lane["name"], "live_gbpusd_m1_snake_microharvest_951795")
        self.assertEqual(lane["state_path"], "reports/live_gbpusd_m1_snake_microharvest_951795_state.json")
        self.assertEqual(lane["event_path"], "reports/live_gbpusd_m1_snake_microharvest_951795_events.jsonl")
        self.assertEqual(lane["restart_args"][lane["restart_args"].index("--live-magic") + 1], "951795")
        self.assertEqual(lane["contract_meta"]["live_magic"], 951795)

    def test_deactivate_other_family_rows_pauses_superseded_contracts(self) -> None:
        registry = {
            "lanes": [
                {"name": "live_gbpusd_m1_snake_microharvest_941795", "enabled": True, "pause_note": ""},
                {"name": "live_gbpusd_m1_snake_microharvest_951795", "enabled": True, "pause_note": ""},
                {"name": "live_eurusd_m1_snake_microharvest_941796", "enabled": True, "pause_note": ""},
            ]
        }
        watchdog = {
            "groups": {
                "fx_watchdog": {
                    "lanes": [
                        "live_gbpusd_m1_snake_microharvest_941795",
                        "live_gbpusd_m1_snake_microharvest_951795",
                        "live_eurusd_m1_snake_microharvest_941796",
                    ]
                }
            },
            "fx_watchdog": {
                "lanes": [
                    "live_gbpusd_m1_snake_microharvest_941795",
                    "live_gbpusd_m1_snake_microharvest_951795",
                    "live_eurusd_m1_snake_microharvest_941796",
                ]
            },
        }
        changed = launcher.deactivate_other_family_rows(
            registry,
            watchdog,
            family_prefix="live_gbpusd_m1_snake_microharvest_",
            keep_lane_name="live_gbpusd_m1_snake_microharvest_951795",
        )
        self.assertTrue(changed)
        old_row = registry["lanes"][0]
        kept_row = registry["lanes"][1]
        self.assertFalse(old_row["enabled"])
        self.assertEqual(old_row["pause_note"], "superseded_by_live_gbpusd_m1_snake_microharvest_951795")
        self.assertTrue(kept_row["enabled"])
        self.assertEqual(
            watchdog["groups"]["fx_watchdog"]["lanes"],
            ["live_gbpusd_m1_snake_microharvest_951795", "live_eurusd_m1_snake_microharvest_941796"],
        )
        self.assertEqual(
            watchdog["fx_watchdog"]["lanes"],
            ["live_gbpusd_m1_snake_microharvest_951795", "live_eurusd_m1_snake_microharvest_941796"],
        )

    def test_cutover_requires_superseded_family_rows_to_be_broker_flat(self) -> None:
        registry = {
            "lanes": [
                {
                    "name": "live_gbpusd_m1_snake_microharvest_941795",
                    "restart_args": ["scripts/live_snake_counter_web_shadow.py", "--live-magic", "941795"],
                },
                {
                    "name": "live_gbpusd_m1_snake_microharvest_951795",
                    "restart_args": ["scripts/live_snake_counter_web_shadow.py", "--live-magic", "951795"],
                },
            ]
        }
        with (
            patch.object(launcher.mt5_terminal_guard, "initialize_mt5", return_value=(True, {"reason": "ok"})),
            patch.object(launcher.live_mirror, "broker_live_positions", return_value=[{"ticket": 1}]),
            patch.object(launcher.mt5, "shutdown"),
        ):
            with self.assertRaisesRegex(RuntimeError, "still has 1 broker positions"):
                launcher.ensure_cutover_rows_are_broker_flat(
                    registry,
                    family_prefix="live_gbpusd_m1_snake_microharvest_",
                    keep_lane_name="live_gbpusd_m1_snake_microharvest_951795",
                )


if __name__ == "__main__":
    unittest.main()
