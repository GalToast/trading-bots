#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_gbpusd_adaptive_first_path_board as board


class BuildGbpusdAdaptiveFirstPathBoardTests(unittest.TestCase):
    def test_build_payload_summarizes_launch_gap_state(self) -> None:
        payload = board.build_payload(
            {
                "status": "packet_defined_waiting_launch",
                "summary": {
                    "research_posture": "shadow_ready_not_started",
                    "forward_gate": "waiting_first_launch",
                },
                "packet_contract": {
                    "lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                    "state_path": "reports/gbp_state.json",
                    "event_path": "reports/gbp_events.jsonl",
                    "command": ["python", "runner.py"],
                    "step": 0.0003,
                    "step_buy": 0.0004,
                    "step_sell": 0.0002,
                    "raw_close_alpha": 0.5,
                    "raw_rearm_variant": "rearm_lvl2_exc1",
                    "raw_sell_gap": 1,
                    "raw_buy_gap": 3,
                },
            },
            {
                "rows": [
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "action_status": "hold_launch_packet_defined_not_started",
                        "first_path_verdict": "awaiting_first_trade_path_event",
                        "first_path_rationale": "No open_ticket or close-like event exists yet in the direct packet log.",
                    }
                ]
            },
            {
                "checked_at": "2026-04-16T15:50:23+00:00",
                "gbp_action_status": "hold_launch_packet_defined_not_started",
                "gbp_first_path_verdict": "awaiting_first_trade_path_event",
                "gbp_first_path_rationale": "No open_ticket or close-like event exists yet in the direct packet log.",
                "gbp_current_run_trade_opens": 0,
                "gbp_current_run_trade_closes": 0,
                "gbp_pre_start_trade_opens": 0,
                "gbp_pre_start_trade_closes": 0,
            },
            {
                "tasks": [
                    {
                        "task_id": "gbpusd_adaptive_comparison_packet",
                        "priority": 4,
                        "status": "ready",
                        "lane": "shadow FX",
                        "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                        "profit_mode": "trend_harvest",
                        "next_action_class": "shadow_compare_and_score",
                        "why": "launch and collect first proof",
                    }
                ]
            },
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "current_live_holder_lane": "live_rearm_941777",
                        "current_live_holder_booked_usd": 724.72,
                        "seat_unblocker_action": "complete_challenger_comparison",
                        "seat_unblocker_read": "Finish comparison data first.",
                        "seat_actionability_status": "queue_ready_actionable",
                        "seat_actionability_read": "Immediately executable seat action.",
                        "seat_contract_gap_status": "queue_backed_actionable",
                        "seat_contract_gap_read": "Already queue backed.",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "Execution-ready on current passive evidence.",
                    }
                ]
            },
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "adaptive_profit_mode": "trend_harvest",
                        "adaptive_runtime_status": "hold_launch_packet_defined_not_started",
                        "adaptive_runtime_overlay_read": "",
                        "why": "blocked by launch/runtime only",
                    }
                ]
            },
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "comparison_verdict": "no_adaptive_score",
                        "shared_score_ready": False,
                        "score_gap": None,
                        "adaptive": {
                            "basis": "missing",
                            "first_path_verdict": "awaiting_first_trade_path_event",
                            "score_unavailable_reason": "adaptive_profit_basis_missing",
                        },
                        "why": "A credible adaptive challenger exists, but it is still blocked by launch/runtime status.",
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "candidate_id": "gbpusd_adaptive_comparison_packet",
                        "verdict": "shadow_ready",
                        "candidate_read": "Shadow-ready FX comparison branch.",
                        "queue_status": "ready",
                        "checks": [
                            {"check_id": "forward_proof_integrity", "status": "warn", "read": "Still needs fresh branch-local comparison proof."}
                        ],
                    }
                ]
            },
            now=datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["summary"]["proof_gate_status"], "packet_defined_waiting_launch")
        self.assertEqual(payload["summary"]["seat_actionability_status"], "queue_ready_actionable")
        self.assertEqual(payload["summary"]["seat_execution_gate_status"], "ready_for_seat_execution")
        self.assertEqual(payload["summary"]["shared_score_verdict"], "no_adaptive_score")
        self.assertEqual(payload["summary"]["acceptance_verdict"], "shadow_ready")
        self.assertEqual(payload["summary"]["runtime_truth_source_status"], "watcher_state_fresh")
        self.assertEqual(payload["overnight_runtime"]["source"], "reports\\adaptive_overnight_launch_packet_monitor_state.json")

    def test_build_payload_promotes_recorded_first_path_before_score_ready(self) -> None:
        payload = board.build_payload(
            {"packet_contract": {"lane_name": "shadow_gbpusd_m15_trend_harvest_v1"}, "summary": {}, "status": "packet_defined_waiting_launch"},
            {"rows": [{"packet_id": "gbpusd_adaptive_comparison_packet"}]},
            {
                "checked_at": "2026-04-16T15:55:00+00:00",
                "gbp_action_status": "already_running_monitor_only",
                "gbp_first_path_verdict": "green_and_monetized",
                "gbp_first_path_rationale": "First path closed green.",
                "gbp_current_run_trade_opens": 1,
                "gbp_current_run_trade_closes": 1,
                "gbp_pre_start_trade_opens": 0,
                "gbp_pre_start_trade_closes": 0,
            },
            {"tasks": []},
            {"rows": [{"symbol": "GBPUSD"}]},
            {"rows": [{"symbol": "GBPUSD"}]},
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "comparison_verdict": "no_adaptive_score",
                        "adaptive": {"basis": "missing"},
                    }
                ]
            },
            {"candidates": []},
            now=datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["summary"]["proof_gate_status"], "first_path_recorded_wait_shared_score_refresh")

    def test_build_payload_marks_shared_score_comparable_when_basis_exists(self) -> None:
        payload = board.build_payload(
            {"packet_contract": {"lane_name": "shadow_gbpusd_m15_trend_harvest_v1"}, "summary": {}, "status": "packet_defined_waiting_launch"},
            {"rows": [{"packet_id": "gbpusd_adaptive_comparison_packet"}]},
            {
                "checked_at": "2026-04-16T15:55:00+00:00",
                "gbp_action_status": "already_running_monitor_only",
                "gbp_first_path_verdict": "green_and_monetized",
                "gbp_first_path_rationale": "First path closed green.",
                "gbp_current_run_trade_opens": 1,
                "gbp_current_run_trade_closes": 1,
                "gbp_pre_start_trade_opens": 0,
                "gbp_pre_start_trade_closes": 0,
            },
            {"tasks": []},
            {"rows": [{"symbol": "GBPUSD"}]},
            {"rows": [{"symbol": "GBPUSD"}]},
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "comparison_verdict": "adaptive_preliminarily_leading",
                        "adaptive": {"basis": "first_path_close_realized_pnl"},
                    }
                ]
            },
            {"candidates": []},
            now=datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["summary"]["proof_gate_status"], "shared_score_comparable")

    def test_build_payload_runtime_truth_overrides_stale_launch_gap_copy(self) -> None:
        payload = board.build_payload(
            {"packet_contract": {"lane_name": "shadow_gbpusd_m15_trend_harvest_v1"}, "summary": {}, "status": "packet_defined_waiting_launch"},
            {
                "rows": [
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "action_status": "already_running_monitor_only",
                        "artifact_trade_opens": 12,
                        "artifact_trade_closes": 0,
                        "artifact_pre_start_trade_opens": 0,
                        "artifact_pre_start_trade_closes": 0,
                        "first_path_verdict": "first_path_opened_waiting_close",
                        "first_path_rationale": "A direct packet open_ticket exists, but no close-like event has completed the first path yet.",
                    }
                ]
            },
            {
                "checked_at": "2026-04-16T15:00:00+00:00",
                "gbp_action_status": "hold_launch_packet_defined_not_started",
                "gbp_first_path_verdict": "awaiting_first_trade_path_event",
                "gbp_first_path_rationale": "Stale watcher truth.",
                "gbp_current_run_trade_opens": 0,
                "gbp_current_run_trade_closes": 0,
                "gbp_pre_start_trade_opens": 0,
                "gbp_pre_start_trade_closes": 0,
            },
            {
                "tasks": [
                    {
                        "task_id": "gbpusd_adaptive_comparison_packet",
                        "priority": 4,
                        "status": "ready",
                        "lane": "shadow FX",
                        "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                        "profit_mode": "trend_harvest",
                        "next_action_class": "shadow_compare_and_score",
                        "why": "pre-launch queue copy",
                    }
                ]
            },
            {"rows": [{"symbol": "GBPUSD", "seat_actionability_status": "queue_ready_actionable", "seat_contract_gap_status": "queue_backed_actionable", "seat_execution_gate_status": "ready_for_seat_execution"}]},
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "adaptive_profit_mode": "trend_harvest",
                        "adaptive_runtime_status": "hold_launch_packet_defined_not_started",
                        "adaptive_runtime_overlay_read": "",
                        "why": "blocked by launch/runtime only",
                    }
                ]
            },
            {
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "comparison_verdict": "no_adaptive_score",
                        "shared_score_ready": False,
                        "score_gap": None,
                        "adaptive": {
                            "basis": "missing",
                            "first_path_verdict": "awaiting_first_trade_path_event",
                            "score_unavailable_reason": "adaptive_profit_basis_missing",
                        },
                        "why": "still blocked by launch/runtime",
                    }
                ]
            },
            {"candidates": []},
            now=datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["summary"]["study_status"], "first_path_opened_wait_shared_score_refresh")
        self.assertEqual(payload["study"]["adaptive_runtime_status"], "already_running_monitor_only")
        self.assertIn("no longer launch", payload["queue"]["why"])
        self.assertEqual(payload["shared_score"]["adaptive_first_path_verdict"], "first_path_opened_waiting_close")
        self.assertEqual(payload["shared_score"]["adaptive_score_unavailable_reason"], "adaptive_first_close_not_recorded_yet")

    def test_build_payload_falls_back_when_watcher_state_is_stale(self) -> None:
        payload = board.build_payload(
            {"packet_contract": {"lane_name": "shadow_gbpusd_m15_trend_harvest_v1"}, "summary": {}, "status": "packet_defined_waiting_launch"},
            {
                "rows": [
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "action_status": "already_running_monitor_only",
                        "execution_watchdog_status": "ok",
                        "artifact_trade_opens": 1,
                        "artifact_trade_closes": 0,
                        "artifact_pre_start_trade_opens": 0,
                        "artifact_pre_start_trade_closes": 0,
                        "first_path_verdict": "first_path_opened_waiting_close",
                        "first_path_rationale": "Fresh overnight packet has opened but not closed yet.",
                    }
                ]
            },
            {
                "checked_at": "2026-04-16T15:00:00+00:00",
                "gbp_action_status": "hold_launch_packet_defined_not_started",
                "gbp_first_path_verdict": "awaiting_first_trade_path_event",
                "gbp_first_path_rationale": "Stale watcher truth.",
                "gbp_current_run_trade_opens": 0,
                "gbp_current_run_trade_closes": 0,
                "gbp_pre_start_trade_opens": 0,
                "gbp_pre_start_trade_closes": 0,
            },
            {"tasks": []},
            {"rows": [{"symbol": "GBPUSD"}]},
            {"rows": [{"symbol": "GBPUSD"}]},
            {"rows": [{"symbol": "GBPUSD", "comparison_verdict": "no_adaptive_score", "adaptive": {"basis": "missing"}}]},
            {"candidates": []},
            now=datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["summary"]["runtime_truth_source"], "reports\\adaptive_overnight_launch_packet_board.json")
        self.assertEqual(payload["summary"]["runtime_truth_source_status"], "watcher_state_stale_fallback_to_overnight")
        self.assertEqual(payload["overnight_runtime"]["first_path_verdict"], "first_path_opened_waiting_close")
        self.assertIn("fell back to the overnight packet surface", " ".join(payload["leadership_read"]))

    def test_render_markdown_mentions_key_gbp_fields(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["one"],
                "summary": {
                    "symbol": "GBPUSD",
                    "packet_id": "gbpusd_adaptive_comparison_packet",
                    "adaptive_lane": "shadow_gbpusd_m15_trend_harvest_v1",
                    "proof_gate_status": "packet_defined_waiting_launch",
                    "seat_actionability_status": "queue_ready_actionable",
                    "seat_contract_gap_status": "queue_backed_actionable",
                    "seat_execution_gate_status": "ready_for_seat_execution",
                    "queue_status": "ready",
                    "queue_priority": 4,
                    "overnight_action_status": "hold_launch_packet_defined_not_started",
                    "runtime_truth_source": "reports/adaptive_overnight_launch_packet_monitor_state.json",
                    "runtime_truth_source_status": "watcher_state_fresh",
                    "first_path_verdict": "awaiting_first_trade_path_event",
                    "study_status": "blocked_runtime_or_launch_gap",
                    "shared_score_verdict": "no_adaptive_score",
                    "shared_adaptive_basis": "missing",
                    "acceptance_verdict": "shadow_ready",
                },
                "packet_contract": {
                    "status": "packet_defined_waiting_launch",
                    "research_posture": "shadow_ready_not_started",
                    "forward_gate": "waiting_first_launch",
                    "lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                    "state_path": "reports/gbp_state.json",
                    "event_path": "reports/gbp_events.jsonl",
                    "command": ["python", "runner.py"],
                    "step": 0.0003,
                    "step_buy": 0.0004,
                    "step_sell": 0.0002,
                    "raw_close_alpha": 0.5,
                    "raw_rearm_variant": "rearm_lvl2_exc1",
                    "raw_sell_gap": 1,
                    "raw_buy_gap": 3,
                },
                "seat": {
                    "seat_verdict": "defended_but_contested_live_seat",
                    "incumbent_lane": "live_rearm_941777",
                    "incumbent_booked_usd": 724.72,
                    "seat_unblocker_action": "complete_challenger_comparison",
                    "seat_unblocker_read": "Finish comparison data first.",
                    "seat_actionability_status": "queue_ready_actionable",
                    "seat_actionability_read": "Immediately executable seat action.",
                    "seat_contract_gap_status": "queue_backed_actionable",
                    "seat_contract_gap_read": "Already queue backed.",
                    "seat_execution_gate_status": "ready_for_seat_execution",
                    "seat_execution_gate_read": "Execution-ready on current passive evidence.",
                },
                "queue": {
                    "priority": 4,
                    "status": "ready",
                    "lane": "shadow FX",
                    "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                    "profit_mode": "trend_harvest",
                    "next_action_class": "shadow_compare_and_score",
                    "why": "launch and collect first proof",
                },
                "overnight_runtime": {
                    "source": "reports/adaptive_overnight_launch_packet_monitor_state.json",
                    "source_status": "watcher_state_fresh",
                    "checked_at": "2026-04-16T15:50:23+00:00",
                    "watcher_checked_at": "2026-04-16T15:50:23+00:00",
                    "watcher_age_seconds": 30.0,
                    "watcher_max_age_seconds": 1200,
                    "action_status": "hold_launch_packet_defined_not_started",
                    "execution_watchdog_status": "",
                    "current_run_trade_opens": 0,
                    "current_run_trade_closes": 0,
                    "pre_start_trade_opens": 0,
                    "pre_start_trade_closes": 0,
                    "first_path_verdict": "awaiting_first_trade_path_event",
                    "first_path_rationale": "No open_ticket or close-like event exists yet in the direct packet log.",
                    "first_path_close_realized_pnl": None,
                    "first_path_open_entry_context": "",
                },
                "acceptance": {
                    "verdict": "shadow_ready",
                    "candidate_read": "Shadow-ready FX comparison branch.",
                    "warning_checks": ["forward_proof_integrity"],
                },
                "study": {
                    "study_status": "blocked_runtime_or_launch_gap",
                    "adaptive_profit_mode": "trend_harvest",
                    "adaptive_runtime_status": "hold_launch_packet_defined_not_started",
                    "adaptive_runtime_overlay_read": "",
                    "why": "blocked by launch/runtime only",
                },
                "shared_score": {
                    "comparison_verdict": "no_adaptive_score",
                    "shared_score_ready": False,
                    "score_gap": None,
                    "adaptive_basis": "missing",
                    "adaptive_first_path_verdict": "awaiting_first_trade_path_event",
                    "adaptive_score_unavailable_reason": "adaptive_profit_basis_missing",
                    "why": "still blocked",
                },
                "notes": ["note", "runtime truth outruns stale queue/study/shared-score copy"],
            }
        )

        self.assertIn("GBPUSD Adaptive First-Path Board", markdown)
        self.assertIn("queue_ready_actionable", markdown)
        self.assertIn("ready_for_seat_execution", markdown)
        self.assertIn("hold_launch_packet_defined_not_started", markdown)
        self.assertIn("no_adaptive_score", markdown)
        self.assertIn("watcher_state_fresh", markdown)
        self.assertIn("runtime truth outruns stale queue/study/shared-score copy", markdown)


if __name__ == "__main__":
    unittest.main()
