#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_lane_dashboard as dashboard


class LiveLaneDashboardTests(unittest.TestCase):
    def test_session_usd_per_hour_uses_runner_start(self) -> None:
        rate = dashboard.session_usd_per_hour(
            "2026-04-17T15:00:00+00:00",
            24.0,
            now_dt=dashboard.parse_iso("2026-04-17T17:00:00+00:00"),
        )

        self.assertEqual(rate, 12.0)

    def test_watchdog_report_paths_reads_all_configured_groups(self) -> None:
        original_load_json = dashboard.load_json
        try:
            dashboard.load_json = lambda path: {
                "groups": {
                    "fx_watchdog": {},
                    "crypto_watchdog": {},
                    "feeder_crypto_canary": {},
                }
            } if path == dashboard.WATCHDOG_GROUPS_CONFIG else {}
            paths = dashboard.watchdog_report_paths()
        finally:
            dashboard.load_json = original_load_json

        self.assertEqual(
            paths,
            [
                dashboard.ROOT / "reports" / "watchdog" / "crypto_watchdog_report.json",
                dashboard.ROOT / "reports" / "watchdog" / "feeder_crypto_canary_report.json",
                dashboard.ROOT / "reports" / "watchdog" / "fx_watchdog_report.json",
                dashboard.WATCHDOG_ROOT_PATH,
            ],
        )

    def test_build_live_lane_rows_filters_to_live_kinds(self) -> None:
        watchdog_rows = {
            "live_rearm_941777": {
                "name": "live_rearm_941777",
                "kind": "live_fx",
                "status": "ok",
                "process_ids": [1234],
                "state_path": "reports/live_rearm_state.json",
                "heartbeat_age_seconds": 0.8,
                "heartbeat_at": "2026-04-13T23:31:50+00:00",
                "open_count": 75,
                "scoreboard_total": {"realized_usd": "791.38", "floating_usd": "0", "net_usd": "791.38"},
                "runner": {"started_at": "2026-04-13T23:26:13+00:00"},
            },
            "shadow_fx_close_policy_mixed": {
                "name": "shadow_fx_close_policy_mixed",
                "kind": "shadow_fx",
                "status": "ok",
            },
        }
        execution_rows = {
            "live_rearm_941777": {
                "broker_magic_open_count": 38,
                "close_count": 51,
                "runner_session_trade_realized_usd": 24.0,
                "notes": "fx_grad=live progress=graduated(100.0%)",
            }
        }
        registry_rows = {"live_rearm_941777": {"name": "live_rearm_941777", "enabled": True, "pause_note": ""}}
        original_load_json = dashboard.load_json
        try:
            dashboard.load_json = lambda path: {
                "runner": {"pid": 5678},
            } if path == dashboard.ROOT / "reports" / "live_rearm_state.json" else {}
            rows = dashboard.build_live_lane_rows(watchdog_rows, execution_rows, registry_rows)
        finally:
            dashboard.load_json = original_load_json

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lane"], "live_rearm_941777")
        self.assertEqual(rows[0]["kind"], "live_fx")
        self.assertEqual(rows[0]["pids"], [5678])
        self.assertEqual(rows[0]["broker_open_count"], 38)
        self.assertEqual(rows[0]["close_count"], 51)
        self.assertEqual(rows[0]["booked_usd"], 791.38)
        self.assertEqual(rows[0]["floating_usd"], 0.0)
        self.assertEqual(rows[0]["net_usd"], 791.38)
        self.assertEqual(rows[0]["broker_net_usd"], 791.38)
        self.assertGreaterEqual(rows[0]["fresh_session_usd_per_hour"], 0.0)
        self.assertEqual(rows[0]["evidence_basis"], "graduated_live_reference")
        self.assertEqual(rows[0]["operator_posture"], "keep_live_reference")

    def test_classify_live_lane_marks_carry_weighted_live(self) -> None:
        row = dashboard.classify_live_lane(
            {
                "status": "ok",
                "notes": "clean_forward_since_repair=-18.92/1c, broker_sync_inherited_closes=62/-1248.82, pre_start_state_carry=46c/+1643.67",
                "close_count": 108,
                "managed_open_count": 0,
            }
        )

        self.assertEqual(row["evidence_basis"], "carry_weighted_live")
        self.assertEqual(row["operator_posture"], "require_fresh_forward_sample")

    def test_classify_live_lane_marks_positive_only_hold_as_intentional_hold(self) -> None:
        row = dashboard.classify_live_lane(
            {
                "enabled": True,
                "pause_note": "",
                "status": "quarantined",
                "runner_status": "positive_only_hold_active",
                "notes": "runner_status=positive_only_hold_active symbols=BTCUSD reason=forced_unwind_blocked_negative",
                "close_count": 288,
                "managed_open_count": 1,
            }
        )

        self.assertEqual(row["evidence_basis"], "intentional_hold_live")
        self.assertEqual(row["operator_posture"], "wait_profitable_unwind")

    def test_classify_live_lane_marks_large_unmonetized_hold_as_trapped(self) -> None:
        row = dashboard.classify_live_lane(
            {
                "enabled": True,
                "pause_note": "",
                "status": "quarantined",
                "runner_status": "positive_only_hold_active",
                "notes": "runner_status=positive_only_hold_active symbols=BTCUSD reason=forced_unwind_blocked_negative",
                "close_count": 288,
                "managed_open_count": 10,
                "fresh_session_booked_usd": 0.0,
            }
        )

        self.assertEqual(row["evidence_basis"], "trapped_hold_live")
        self.assertEqual(row["operator_posture"], "manual_review_or_release_capital")

    def test_classify_live_lane_marks_live_contract_friction_invalid(self) -> None:
        row = dashboard.classify_live_lane(
            {
                "enabled": True,
                "pause_note": "",
                "status": "ok",
                "runner_status": "live_contract_friction_invalid",
                "notes": "runner_status=live_contract_friction_invalid spread_to_step=12.88 max_ratio=0.30 blocked=5",
                "close_count": 0,
                "managed_open_count": 0,
            }
        )

        self.assertEqual(row["evidence_basis"], "contract_invalid_live")
        self.assertEqual(row["operator_posture"], "fix_contract_before_recycle")

    def test_classify_live_lane_marks_registry_paused_rows_as_parked(self) -> None:
        row = dashboard.classify_live_lane(
            {
                "enabled": False,
                "pause_note": "decommissioned_toxic_bleed",
                "status": "paused",
                "notes": "",
                "close_count": 0,
                "managed_open_count": 0,
            }
        )

        self.assertEqual(row["evidence_basis"], "decommissioned_or_parked")
        self.assertEqual(row["operator_posture"], "leave_paused")

    def test_build_live_lane_rows_sanitizes_parked_lane_runtime_residue(self) -> None:
        watchdog_rows = {
            "live_btcusd_m15_warp_941781": {
                "name": "live_btcusd_m15_warp_941781",
                "kind": "live_crypto",
                "status": "stale_recurrence",
                "process_ids": [39344],
                "state_path": "reports/penetration_lattice_live_btcusd_m15_warp_state.json",
                "heartbeat_age_seconds": 1194.6,
                "heartbeat_at": "2026-04-17T22:29:59+00:00",
                "open_count": 36,
                "scoreboard_total": {"realized_usd": "2079.26", "floating_usd": "0", "net_usd": "2079.26"},
                "runner": {"started_at": "2026-04-17T22:29:19+00:00"},
            }
        }
        execution_rows = {
            "live_btcusd_m15_warp_941781": {
                "broker_magic_open_count": 0,
                "open_count": 36,
                "close_count": 331,
                "runner_status": "positive_only_hold_active",
                "runner_session_trade_realized_usd": 0.0,
                "notes": "runner_status=positive_only_hold_active symbols=BTCUSD reason=forced_unwind_blocked_negative",
            }
        }
        registry_rows = {
            "live_btcusd_m15_warp_941781": {
                "name": "live_btcusd_m15_warp_941781",
                "enabled": False,
                "pause_note": "manual_capital_release_2026_04_17",
            }
        }
        original_load_json = dashboard.load_json
        try:
            dashboard.load_json = lambda path: {"runner": {"pid": 39344}} if path == dashboard.ROOT / "reports" / "penetration_lattice_live_btcusd_m15_warp_state.json" else {}
            rows = dashboard.build_live_lane_rows(watchdog_rows, execution_rows, registry_rows)
        finally:
            dashboard.load_json = original_load_json

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "paused")
        self.assertEqual(rows[0]["pids"], [])
        self.assertEqual(rows[0]["broker_open_count"], 0)
        self.assertEqual(rows[0]["managed_open_count"], 0)
        self.assertEqual(rows[0]["fresh_session_booked_usd"], 0.0)
        self.assertEqual(rows[0]["fresh_session_usd_per_hour"], 0.0)
        self.assertEqual(rows[0]["notes"], "")
        self.assertEqual(rows[0]["evidence_basis"], "decommissioned_or_parked")

    def test_classify_live_lane_marks_zero_close_probe_as_thin_sample(self) -> None:
        row = dashboard.classify_live_lane(
            {
                "enabled": True,
                "pause_note": "",
                "status": "ok",
                "notes": "",
                "close_count": 0,
                "managed_open_count": 0,
            }
        )

        self.assertEqual(row["evidence_basis"], "thin_live_sample")
        self.assertEqual(row["operator_posture"], "wait_more_sample")

    def test_build_live_lane_rows_prefers_execution_realized_when_scoreboard_is_zero(self) -> None:
        watchdog_rows = {
            "live_eurusd_adaptive_harness_941885": {
                "name": "live_eurusd_adaptive_harness_941885",
                "kind": "live_fx",
                "status": "ok",
                "process_ids": [1234],
                "state_path": "reports/live_eurusd_state.json",
                "heartbeat_age_seconds": 0.5,
                "heartbeat_at": "2026-04-17T16:30:00+00:00",
                "open_count": 8,
                "scoreboard_total": {"realized_usd": "0", "floating_usd": "0", "net_usd": "0"},
                "runner": {"started_at": "2026-04-17T16:00:00+00:00"},
            }
        }
        execution_rows = {
            "live_eurusd_adaptive_harness_941885": {
                "broker_magic_open_count": 8,
                "close_count": 14,
                "pre_start_state_carry_realized_usd": 2.23,
                "runner_session_trade_realized_usd": 15.86,
                "notes": "runner_session_since_start=10c/+15.86 6o, pre_start_state_carry=4c/+2.23",
            }
        }
        registry_rows = {
            "live_eurusd_adaptive_harness_941885": {
                "name": "live_eurusd_adaptive_harness_941885",
                "enabled": True,
                "pause_note": "",
            }
        }
        original_load_json = dashboard.load_json
        try:
            dashboard.load_json = lambda path: {
                "runner": {"pid": 5678},
            } if path == dashboard.ROOT / "reports" / "live_eurusd_state.json" else {}
            rows = dashboard.build_live_lane_rows(watchdog_rows, execution_rows, registry_rows)
        finally:
            dashboard.load_json = original_load_json

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["booked_usd"], 18.09)
        self.assertEqual(rows[0]["broker_net_usd"], 18.09)

    def test_build_live_lane_rows_displays_runner_session_closes_for_inherited_history_lanes(self) -> None:
        watchdog_rows = {
            "live_eurusd_m1_snake_microharvest_941796": {
                "name": "live_eurusd_m1_snake_microharvest_941796",
                "kind": "live_fx",
                "status": "ok",
                "process_ids": [1234],
                "state_path": "reports/live_eurusd_m1_snake_microharvest_state.json",
                "heartbeat_age_seconds": 0.5,
                "heartbeat_at": "2026-04-17T19:44:00+00:00",
                "open_count": 16,
                "scoreboard_total": {"realized_usd": "0", "floating_usd": "0", "net_usd": "0"},
                "runner": {"started_at": "2026-04-17T19:00:00+00:00"},
            }
        }
        execution_rows = {
            "live_eurusd_m1_snake_microharvest_941796": {
                "broker_magic_open_count": 16,
                "open_count": 16,
                "close_count": 0,
                "runner_session_trade_closes": 18,
                "runner_session_trade_realized_usd": 3.92,
                "notes": "broker_sync_inherited_closes=460/+26.22, runner_session_since_start=18c/+3.92 18o",
            }
        }
        registry_rows = {
            "live_eurusd_m1_snake_microharvest_941796": {
                "name": "live_eurusd_m1_snake_microharvest_941796",
                "enabled": True,
                "pause_note": "",
            }
        }
        original_load_json = dashboard.load_json
        try:
            dashboard.load_json = lambda path: {
                "runner": {"pid": 5678},
            } if path == dashboard.ROOT / "reports" / "live_eurusd_m1_snake_microharvest_state.json" else {}
            rows = dashboard.build_live_lane_rows(watchdog_rows, execution_rows, registry_rows)
        finally:
            dashboard.load_json = original_load_json

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close_count"], 0)
        self.assertEqual(rows[0]["runner_session_trade_closes"], 18)
        self.assertEqual(rows[0]["display_close_count"], 18)
        self.assertEqual(rows[0]["evidence_basis"], "inherited_history_live")

    def test_build_live_lane_rows_uses_realized_not_floating_in_booked_alias(self) -> None:
        watchdog_rows = {
            "live_btcusd_m15_warp_941781": {
                "name": "live_btcusd_m15_warp_941781",
                "kind": "live_crypto",
                "status": "quarantined",
                "process_ids": [1234],
                "state_path": "reports/live_btc_state.json",
                "heartbeat_age_seconds": 0.5,
                "heartbeat_at": "2026-04-17T17:12:00+00:00",
                "open_count": 10,
                "scoreboard_total": {"realized_usd": "1456.94", "floating_usd": "224.30", "net_usd": "1681.24"},
                "runner": {"started_at": "2026-04-17T17:02:27+00:00"},
            }
        }
        execution_rows = {
            "live_btcusd_m15_warp_941781": {
                "broker_magic_open_count": 10,
                "open_count": 10,
                "close_count": 288,
                "runner_status": "positive_only_hold_active",
                "notes": "runner_status=positive_only_hold_active symbols=BTCUSD reason=forced_unwind_blocked_negative",
            }
        }
        registry_rows = {
            "live_btcusd_m15_warp_941781": {
                "name": "live_btcusd_m15_warp_941781",
                "enabled": True,
                "pause_note": "",
            }
        }
        original_load_json = dashboard.load_json
        try:
            dashboard.load_json = lambda path: {
                "runner": {"pid": 5678},
            } if path == dashboard.ROOT / "reports" / "live_btc_state.json" else {}
            rows = dashboard.build_live_lane_rows(watchdog_rows, execution_rows, registry_rows)
        finally:
            dashboard.load_json = original_load_json

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["booked_usd"], 1456.94)
        self.assertEqual(rows[0]["floating_usd"], 224.30)
        self.assertEqual(rows[0]["net_usd"], 1681.24)
        self.assertEqual(rows[0]["broker_net_usd"], 1456.94)

    def test_markdown_mentions_evidence_basis_summary(self) -> None:
        markdown = dashboard.markdown_from_payload(
            {
                "generated_at": "2026-04-16T04:00:00+00:00",
                "summary": {
                    "total_live_lanes": 2,
                    "ok_lanes": 2,
                    "non_ok_lanes": 0,
                    "graduated_live_reference_count": 1,
                    "trapped_hold_live_count": 1,
                    "intentional_hold_live_count": 1,
                    "contract_invalid_live_count": 0,
                    "carry_weighted_live_count": 1,
                    "thin_live_sample_count": 0,
                    "decommissioned_or_parked_count": 0,
                },
                "rows": [
                    {
                        "lane": "live_rearm_941777",
                        "kind": "live_fx",
                        "status": "ok",
                        "evidence_basis": "graduated_live_reference",
                        "operator_posture": "keep_live_reference",
                        "pids": [123],
                        "heartbeat_age_seconds": 0.3,
                        "broker_open_count": 4,
                        "managed_open_count": 4,
                        "outside_scope_open_count": 0,
                        "close_count": 320,
                        "fresh_session_booked_usd": 12.34,
                        "fresh_session_usd_per_hour": 6.17,
                        "booked_usd": 724.43,
                        "floating_usd": 0.0,
                        "net_usd": 724.43,
                        "broker_net_usd": 724.43,
                        "notes": "fx_grad=live progress=graduated(100.0%)",
                    }
                ],
            }
        )

        self.assertIn("graduated=1", markdown)
        self.assertIn("trapped=1", markdown)
        self.assertIn("hold=1", markdown)
        self.assertIn("invalid=0", markdown)
        self.assertIn("carry_weighted=1", markdown)
        self.assertIn("thin=0", markdown)
        self.assertIn("parked=0", markdown)
        self.assertIn("Fresh Booked USD", markdown)
        self.assertIn("Fresh $/hr", markdown)
        self.assertIn("Booked USD", markdown)
        self.assertIn("Floating USD", markdown)
        self.assertIn("Net USD", markdown)
        self.assertIn("Evidence Basis", markdown)
        self.assertIn("graduated_live_reference", markdown)
        self.assertIn("trapped_hold_live", markdown)
        self.assertIn("intentional_hold_live", markdown)
        self.assertIn("contract_invalid_live", markdown)
        self.assertIn("thin_live_sample", markdown)


if __name__ == "__main__":
    unittest.main()
