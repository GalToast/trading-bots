from __future__ import annotations

import unittest
from unittest.mock import patch

import scripts.watch_adaptive_overnight_launch_packet_board as watcher


class WatchAdaptiveOvernightLaunchPacketBoardTests(unittest.TestCase):
    def test_snapshot_from_payload_extracts_restore_truth(self) -> None:
        payload = {
            "summary": {
                "launch_now_lanes": [],
                "already_running_lanes": ["shadow_btcusd_m15_warp_restore_v1"],
                "hold_lanes": ["shadow_btcusd_m15_adaptive_regime", "shadow_gbpusd_m15_trend_harvest_v1"],
            },
            "rows": [
                {
                    "packet_id": "btc_restore_comparison_shadow",
                    "action_status": "already_running_monitor_only",
                    "execution_watchdog_status": "quarantined",
                    "artifact_started": True,
                    "artifact_runner_started_at": "2026-04-16T05:50:25+00:00",
                    "artifact_runner_heartbeat_age_seconds": 15.0,
                    "artifact_trade_opens": 0,
                    "artifact_trade_closes": 0,
                    "artifact_pre_start_trade_opens": 13,
                    "artifact_pre_start_trade_closes": 13,
                    "first_path_verdict": "awaiting_first_trade_path_event",
                    "first_path_rationale": "No open_ticket or close-like event exists yet in the direct packet log.",
                    "first_path_close_realized_pnl": None,
                    "first_path_open_entry_context": "",
                },
                {
                    "packet_id": "gbpusd_adaptive_comparison_packet",
                    "action_status": "hold_launch_packet_defined_not_started",
                    "execution_watchdog_status": "",
                    "artifact_started": False,
                    "artifact_runner_started_at": "",
                    "artifact_runner_heartbeat_age_seconds": None,
                    "artifact_trade_opens": 0,
                    "artifact_trade_closes": 0,
                    "artifact_pre_start_trade_opens": 0,
                    "artifact_pre_start_trade_closes": 0,
                    "first_path_verdict": "awaiting_first_trade_path_event",
                    "first_path_rationale": "No open_ticket or close-like event exists yet in the direct packet log.",
                    "first_path_close_realized_pnl": None,
                    "first_path_open_entry_context": "",
                }
            ],
        }

        snapshot = watcher.snapshot_from_payload(payload)

        self.assertEqual(snapshot["already_running_lanes"], ["shadow_btcusd_m15_warp_restore_v1"])
        self.assertEqual(snapshot["restore_action_status"], "already_running_monitor_only")
        self.assertEqual(snapshot["restore_execution_watchdog_status"], "quarantined")
        self.assertEqual(snapshot["restore_current_run_trade_opens"], 0)
        self.assertEqual(snapshot["restore_pre_start_trade_opens"], 13)
        self.assertEqual(snapshot["restore_first_path_verdict"], "awaiting_first_trade_path_event")
        self.assertEqual(snapshot["gbp_action_status"], "hold_launch_packet_defined_not_started")
        self.assertEqual(snapshot["gbp_current_run_trade_closes"], 0)
        self.assertEqual(snapshot["gbp_first_path_verdict"], "awaiting_first_trade_path_event")

    def test_diff_messages_reports_restore_path_changes(self) -> None:
        previous = {
            "launch_now_lanes": ["shadow_btcusd_m15_warp_restore_v1"],
            "already_running_lanes": [],
            "restore_action_status": "launch_now_manual_packet",
            "restore_execution_watchdog_status": "",
            "restore_current_run_trade_opens": 0,
            "restore_current_run_trade_closes": 0,
            "restore_first_path_verdict": "awaiting_first_trade_path_event",
            "restore_first_path_close_realized_pnl": None,
        }
        current = {
            "launch_now_lanes": [],
            "already_running_lanes": ["shadow_btcusd_m15_warp_restore_v1"],
            "restore_action_status": "already_running_monitor_only",
            "restore_execution_watchdog_status": "ok",
            "restore_current_run_trade_opens": 1,
            "restore_current_run_trade_closes": 1,
            "restore_first_path_verdict": "green_and_monetized",
            "restore_first_path_close_realized_pnl": 12.5,
        }

        changes = watcher.diff_messages(previous, current)

        self.assertIn("launch_now_lanes ['shadow_btcusd_m15_warp_restore_v1'] -> []", changes)
        self.assertIn("already_running_lanes [] -> ['shadow_btcusd_m15_warp_restore_v1']", changes)
        self.assertIn("restore_action_status launch_now_manual_packet -> already_running_monitor_only", changes)
        self.assertIn("restore_execution_watchdog_status missing -> ok", changes)
        self.assertIn("restore_current_run_trade_opens 0 -> 1", changes)
        self.assertIn("restore_current_run_trade_closes 0 -> 1", changes)
        self.assertIn("restore_first_path_verdict awaiting_first_trade_path_event -> green_and_monetized", changes)

    def test_diff_messages_reports_gbp_path_changes(self) -> None:
        previous = {
            "gbp_action_status": "hold_launch_packet_defined_not_started",
            "gbp_execution_watchdog_status": "",
            "gbp_current_run_trade_opens": 0,
            "gbp_current_run_trade_closes": 0,
            "gbp_first_path_verdict": "awaiting_first_trade_path_event",
            "gbp_first_path_close_realized_pnl": None,
            "gbp_proof_gate_status": "packet_defined_waiting_launch",
            "gbp_queue_status": "ready",
            "gbp_queue_next_action_class": "shadow_compare_and_score",
            "gbp_seat_actionability_status": "queue_ready_actionable",
            "gbp_seat_contract_gap_status": "queue_backed_actionable",
            "gbp_seat_execution_gate_status": "ready_for_seat_execution",
            "gbp_seat_execution_gate_read": "Execution-ready on current passive evidence.",
            "gbp_shared_score_verdict": "no_adaptive_score",
        }
        current = {
            "gbp_action_status": "already_running_monitor_only",
            "gbp_execution_watchdog_status": "ok",
            "gbp_current_run_trade_opens": 1,
            "gbp_current_run_trade_closes": 1,
            "gbp_first_path_verdict": "green_and_monetized",
            "gbp_first_path_close_realized_pnl": 6.25,
            "gbp_proof_gate_status": "shared_score_comparable",
            "gbp_queue_status": "ready",
            "gbp_queue_next_action_class": "score_and_compare",
            "gbp_seat_actionability_status": "queue_ready_actionable",
            "gbp_seat_contract_gap_status": "queue_backed_actionable",
            "gbp_seat_execution_gate_status": "execution_gate_temporarily_revoked",
            "gbp_seat_execution_gate_read": "Proof path regressed and needs review.",
            "gbp_shared_score_verdict": "adaptive_candidate_preliminarily_leading",
        }

        changes = watcher.diff_messages(previous, current)

        self.assertIn("gbp_action_status hold_launch_packet_defined_not_started -> already_running_monitor_only", changes)
        self.assertIn("gbp_execution_watchdog_status missing -> ok", changes)
        self.assertIn("gbp_current_run_trade_opens 0 -> 1", changes)
        self.assertIn("gbp_current_run_trade_closes 0 -> 1", changes)
        self.assertIn("gbp_first_path_verdict awaiting_first_trade_path_event -> green_and_monetized", changes)
        self.assertIn("gbp_first_path_close_realized_pnl missing -> 6.25", changes)
        self.assertIn("gbp_proof_gate_status packet_defined_waiting_launch -> shared_score_comparable", changes)
        self.assertIn("gbp_queue_next_action_class shadow_compare_and_score -> score_and_compare", changes)
        self.assertIn("gbp_seat_execution_gate_status ready_for_seat_execution -> execution_gate_temporarily_revoked", changes)
        self.assertIn(
            "gbp_seat_execution_gate_read Execution-ready on current passive evidence. -> Proof path regressed and needs review.",
            changes,
        )
        self.assertIn("gbp_shared_score_verdict no_adaptive_score -> adaptive_candidate_preliminarily_leading", changes)

    def test_diff_messages_reports_gbp_execution_gate_read_even_when_status_is_unchanged(self) -> None:
        previous = {
            "gbp_seat_execution_gate_status": "ready_for_seat_execution",
            "gbp_seat_execution_gate_read": "Execution-ready on current passive evidence.",
        }
        current = {
            "gbp_seat_execution_gate_status": "ready_for_seat_execution",
            "gbp_seat_execution_gate_read": "Execution-ready, but only while queue alignment remains intact.",
        }

        changes = watcher.diff_messages(previous, current)

        self.assertNotIn(
            "gbp_seat_execution_gate_status ready_for_seat_execution -> ready_for_seat_execution",
            changes,
        )
        self.assertIn(
            "gbp_seat_execution_gate_read Execution-ready on current passive evidence. -> Execution-ready, but only while queue alignment remains intact.",
            changes,
        )

    def test_proof_arrived_requires_close_like_verdict(self) -> None:
        self.assertFalse(watcher.proof_arrived({"restore_first_path_verdict": ""}))
        self.assertFalse(watcher.proof_arrived({"restore_first_path_verdict": "awaiting_first_trade_path_event"}))
        self.assertFalse(watcher.proof_arrived({"restore_first_path_verdict": "first_path_opened_waiting_close"}))
        self.assertTrue(watcher.proof_arrived({"restore_first_path_verdict": "green_and_monetized"}))
        self.assertTrue(watcher.proof_arrived({"gbp_first_path_verdict": "green_and_monetized"}))
        self.assertTrue(watcher.proof_arrived({"gbp_proof_gate_status": "shared_score_comparable"}))
        self.assertTrue(
            watcher.proof_arrived(
                {
                    "restore_first_path_verdict": "awaiting_first_trade_path_event",
                    "btc_max_profit_adaptive_close_count": 1,
                }
            )
        )

    def test_format_switchboard_message_includes_restore_summary(self) -> None:
        content = watcher.format_switchboard_message(
            [
                "restore_first_path_verdict awaiting_first_trade_path_event -> never_green_toxic_continuation",
                "btc_max_profit_verdict adaptive_candidate_defined_but_unproven -> adaptive_candidate_preliminarily_leading",
            ],
            {
                "launch_now_lanes": [],
                "already_running_lanes": ["shadow_btcusd_m15_warp_restore_v1"],
                "restore_action_status": "already_running_monitor_only",
                "restore_execution_watchdog_status": "quarantined",
                "restore_current_run_trade_opens": 0,
                "restore_current_run_trade_closes": 1,
                "restore_pre_start_trade_opens": 13,
                "restore_pre_start_trade_closes": 13,
                "restore_first_path_verdict": "never_green_toxic_continuation",
                "restore_first_path_rationale": "The first close-like event realized a loss without any recorded first-green transition.",
                "gbp_action_status": "hold_launch_packet_defined_not_started",
                "gbp_execution_watchdog_status": "",
                "gbp_current_run_trade_opens": 0,
                "gbp_current_run_trade_closes": 0,
                "gbp_pre_start_trade_opens": 0,
                "gbp_pre_start_trade_closes": 0,
                "gbp_first_path_verdict": "awaiting_first_trade_path_event",
                "gbp_proof_gate_status": "packet_defined_waiting_launch",
                "gbp_queue_next_action_class": "shadow_compare_and_score",
                "gbp_seat_execution_gate_status": "ready_for_seat_execution",
                "gbp_seat_execution_gate_read": "Execution-ready on current passive evidence.",
                "gbp_shared_score_verdict": "no_adaptive_score",
                "btc_max_profit_verdict": "adaptive_candidate_preliminarily_leading",
                "btc_max_profit_adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                "btc_max_profit_adaptive_close_count": 1,
                "btc_max_profit_adaptive_realized_usd": 8.5,
            },
        )

        self.assertIn("already_running=['shadow_btcusd_m15_warp_restore_v1']", content)
        self.assertIn("btc_restore_action=already_running_monitor_only", content)
        self.assertIn("btc_restore_current_run=0/1", content)
        self.assertIn("btc_restore_pre_start_history=13/13", content)
        self.assertIn("btc_restore_first_path=never_green_toxic_continuation", content)
        self.assertIn("gbp_action=hold_launch_packet_defined_not_started", content)
        self.assertIn("gbp_current_run=0/0", content)
        self.assertIn("gbp_first_path=awaiting_first_trade_path_event", content)
        self.assertIn("gbp_proof_gate=packet_defined_waiting_launch", content)
        self.assertIn("gbp_next_action=shadow_compare_and_score", content)
        self.assertIn("gbp_execution_gate=ready_for_seat_execution", content)
        self.assertIn("GBP execution gate:", content)
        self.assertIn("gbp_shared_score=no_adaptive_score", content)
        self.assertIn("btc_max_profit_verdict=adaptive_candidate_preliminarily_leading", content)
        self.assertIn("btc_max_profit_adaptive=btcusd_rangeatr_cash_harvest_v1", content)
        self.assertIn("BTC rationale:", content)
        self.assertIn("Changes:", content)

    def test_enrich_snapshot_adds_btc_profit_contract_fields(self) -> None:
        with patch.object(
            watcher,
            "load_json",
            side_effect=[
                {
                    "summary": {
                        "proof_gate_status": "packet_defined_waiting_launch",
                        "queue_status": "ready",
                        "seat_actionability_status": "queue_ready_actionable",
                        "seat_contract_gap_status": "queue_backed_actionable",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "shared_score_verdict": "no_adaptive_score",
                    },
                    "seat": {"seat_execution_gate_read": "Execution-ready on current passive evidence."},
                    "queue": {"next_action_class": "shadow_compare_and_score"},
                },
                {
                    "rows": [
                        {
                            "symbol": "BTCUSD",
                            "btc_max_profit_comparison": {
                                "verdict": "adaptive_candidate_defined_but_unproven",
                                "restore_lane": "shadow_btcusd_m15_warp_restore_v1",
                                "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                                "adaptive_runner_session_close_count": 0,
                                "adaptive_runner_session_realized_usd": 0.0,
                                "adaptive_pre_start_carry_realized_usd": -17.77,
                                "score_gap": 1,
                                "read": "btc contract read",
                            },
                        }
                    ]
                },
                {"summary": {"btc_max_profit_verdict": "adaptive_candidate_defined_but_unproven"}},
                {"summary": {"btc_max_profit_verdict": "adaptive_candidate_defined_but_unproven"}},
            ],
        ):
            snapshot = watcher.enrich_snapshot({"restore_action_status": "already_running_monitor_only"})

        self.assertEqual(snapshot["gbp_proof_gate_status"], "packet_defined_waiting_launch")
        self.assertEqual(snapshot["gbp_queue_next_action_class"], "shadow_compare_and_score")
        self.assertEqual(snapshot["gbp_seat_execution_gate_status"], "ready_for_seat_execution")
        self.assertEqual(snapshot["gbp_seat_execution_gate_read"], "Execution-ready on current passive evidence.")
        self.assertEqual(snapshot["gbp_shared_score_verdict"], "no_adaptive_score")
        self.assertEqual(snapshot["btc_max_profit_verdict"], "adaptive_candidate_defined_but_unproven")
        self.assertEqual(snapshot["btc_max_profit_adaptive_shape_id"], "btcusd_rangeatr_cash_harvest_v1")
        self.assertEqual(snapshot["btc_max_profit_adaptive_close_count"], 0)
        self.assertEqual(snapshot["acceptance_btc_max_profit_verdict"], "adaptive_candidate_defined_but_unproven")

    def test_refresh_scripts_include_gbp_shadow_packet_before_gbp_first_path_board(self) -> None:
        script_names = [script.name for script in watcher.REFRESH_SCRIPTS]

        self.assertEqual(script_names[0], "build_adaptive_overnight_launch_packet_board.py")
        self.assertIn("build_gbpusd_adaptive_shadow_packet.py", script_names)
        self.assertIn("build_gbpusd_adaptive_first_path_board.py", script_names)
        self.assertLess(
            script_names.index("build_gbpusd_adaptive_shadow_packet.py"),
            script_names.index("build_gbpusd_adaptive_first_path_board.py"),
        )


if __name__ == "__main__":
    unittest.main()
