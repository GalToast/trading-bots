from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_execution_ready_blind_spot_board as board


class BuildExecutionReadyBlindSpotBoardTests(unittest.TestCase):
    def test_build_payload_prefers_gbp_but_surfaces_gbp_and_usdjpy_failure_modes(self) -> None:
        payload = board.build_payload(
            seat_board={
                "summary": {"highest_execution_ready_symbol": "GBPUSD"},
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "GBP can execute now.",
                    },
                    {
                        "symbol": "USDJPY",
                        "seat_verdict": "no_live_seat",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "USDJPY can execute now.",
                    },
                ],
            },
            next_action_board={
                "summary": {"highest_launch_now_symbol": "GBPUSD"},
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "queue_task_status": "ready",
                        "queue_task_priority": 4,
                        "profit_mode": "trend_harvest",
                        "next_action_class": "shadow_compare_and_score",
                    },
                    {
                        "symbol": "USDJPY",
                        "queue_task_id": "usdjpy_bounded_forward_proof",
                        "queue_task_status": "ready",
                        "queue_task_priority": 6,
                        "profit_mode": "friction_survivor",
                        "next_action_class": "prove_executability_and_survival_before_promotion",
                    },
                ],
            },
            gbp_first_path_board={
                "summary": {"proof_gate_status": "packet_defined_waiting_launch"},
                "overnight_runtime": {
                    "action_status": "hold_launch_packet_defined_not_started",
                    "first_path_verdict": "awaiting_first_trade_path_event",
                },
                "acceptance": {
                    "verdict": "shadow_ready",
                    "warning_checks": [
                        "early_green_monetization",
                        "forward_proof_integrity",
                    ],
                },
                "shared_score": {
                    "comparison_verdict": "no_adaptive_score",
                    "adaptive_basis": "missing",
                    "adaptive_first_path_verdict": "awaiting_first_trade_path_event",
                },
            },
            study_board={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "incumbent_lane": "live_rearm_941777",
                        "incumbent_seat_verdict": "defended_but_contested_live_seat",
                        "adaptive_runtime_status": "hold_launch_packet_defined_not_started",
                    },
                    {
                        "symbol": "USDJPY",
                        "study_status": "adaptive_candidate_without_incumbent",
                        "incumbent_present": False,
                        "adaptive_runtime_status": "hold_disabled_proof_candidate",
                        "adaptive_lane": "shadow_usdjpy_shallow03",
                    },
                ],
            },
            shared_score_board={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "comparison_verdict": "no_adaptive_score",
                    },
                    {
                        "symbol": "USDJPY",
                        "study_status": "adaptive_candidate_without_incumbent",
                        "comparison_verdict": "no_incumbent_score",
                        "adaptive": {"lane": "shadow_usdjpy_shallow03"},
                    },
                ],
            },
            acceptance_board={
                "candidates": [
                    {
                        "candidate_id": "gbpusd_adaptive_comparison_packet",
                        "verdict": "shadow_ready",
                        "warning_checks": [
                            "early_green_monetization",
                            "portfolio_governance",
                            "forward_proof_integrity",
                        ],
                    },
                    {
                        "candidate_id": "usdjpy_bounded_forward_proof",
                        "verdict": "research_only",
                        "candidate_read": "Research-only until fresh bounded proof lands.",
                        "warning_checks": ["forward_proof_integrity"],
                    },
                ]
            },
            overnight_board={
                "rows": [
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                        "action_status": "hold_launch_packet_defined_not_started",
                    },
                    {
                        "packet_id": "usdjpy_bounded_forward_proof",
                        "lane_name": "shadow_usdjpy_gap2",
                        "action_status": "launch_now_manual_packet",
                    },
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["recommended_option"], "advance_gbpusd_first")
        self.assertEqual(summary["recommended_symbol"], "GBPUSD")
        self.assertEqual(summary["parallel_option_status"], "not_recommended_yet")

        rows = {row["symbol"]: row for row in payload["rows"]}
        self.assertIn("launch_not_started", rows["GBPUSD"]["blind_spot_ids"])
        self.assertIn("no_adaptive_score", rows["GBPUSD"]["blind_spot_ids"])
        self.assertIn("lane_identity_split", rows["USDJPY"]["blind_spot_ids"])
        self.assertIn("research_only_acceptance", rows["USDJPY"]["blind_spot_ids"])
        self.assertEqual(rows["USDJPY"]["lane_name"], "shadow_usdjpy_gap2")
        self.assertEqual(rows["USDJPY"]["study_lane_name"], "shadow_usdjpy_shallow03")

    def test_render_markdown_mentions_recommendation_and_lane_split(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "decision_id": 12,
                "title": "Which queue-backed execution-ready seat seam should the room advance first?",
                "summary": {
                    "recommended_option": "advance_gbpusd_first",
                    "recommended_symbol": "GBPUSD",
                    "recommended_verdict": "cleaner_first_move_but_launch_debt",
                    "highest_execution_ready_symbol": "GBPUSD",
                    "highest_launch_now_symbol": "GBPUSD",
                    "parallel_option_status": "not_recommended_yet",
                    "parallel_option_read": "parallel not recommended",
                },
                "leadership_read": ["line"],
                "rows": [
                    {
                        "decision_option": "advance_usdjpy_first",
                        "symbol": "USDJPY",
                        "recommended": False,
                        "adversarial_verdict": "comparability_debt_heavier_than_launch_debt",
                        "why_considered": "why",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_verdict": "no_live_seat",
                        "queue_task_id": "usdjpy_bounded_forward_proof",
                        "queue_task_status": "ready",
                        "profit_mode": "friction_survivor",
                        "next_action_class": "prove_executability_and_survival_before_promotion",
                        "acceptance_verdict": "research_only",
                        "study_status": "adaptive_candidate_without_incumbent",
                        "comparison_verdict": "no_incumbent_score",
                        "runtime_action_status": "launch_now_manual_packet",
                        "adaptive_runtime_status": "hold_disabled_proof_candidate",
                        "lane_name": "shadow_usdjpy_gap2",
                        "study_lane_name": "shadow_usdjpy_shallow03",
                        "shared_score_lane_name": "shadow_usdjpy_shallow03",
                        "blind_spots": [
                            {
                                "blind_spot_id": "lane_identity_split",
                                "severity": "high",
                                "read": "split",
                                "evidence": ["overnight_lane=shadow_usdjpy_gap2"],
                            }
                        ],
                        "proof_debt": ["forward_proof_integrity"],
                        "why_this_option_could_fail": ["fail"],
                        "reversal_triggers": ["trigger"],
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Execution-Ready Blind-Spot Board", markdown)
        self.assertIn("advance_gbpusd_first", markdown)
        self.assertIn("lane_identity_split", markdown)
        self.assertIn("shadow_usdjpy_gap2", markdown)

    def test_build_payload_updates_gbp_blind_spot_once_runtime_is_running(self) -> None:
        payload = board.build_payload(
            seat_board={
                "summary": {"highest_execution_ready_symbol": "GBPUSD"},
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "GBP can execute now.",
                    },
                    {
                        "symbol": "USDJPY",
                        "seat_verdict": "no_live_seat",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "USDJPY can execute now.",
                    },
                ],
            },
            next_action_board={
                "summary": {"highest_launch_now_symbol": "GBPUSD"},
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "queue_task_status": "ready",
                        "queue_task_priority": 4,
                        "profit_mode": "trend_harvest",
                        "next_action_class": "shadow_compare_and_score",
                    },
                    {
                        "symbol": "USDJPY",
                        "queue_task_id": "usdjpy_bounded_forward_proof",
                        "queue_task_status": "ready",
                        "queue_task_priority": 6,
                        "profit_mode": "friction_survivor",
                        "next_action_class": "prove_executability_and_survival_before_promotion",
                    },
                ],
            },
            gbp_first_path_board={
                "summary": {
                    "proof_gate_status": "first_path_recorded_wait_shared_score_refresh",
                    "first_path_verdict": "first_path_opened_waiting_close",
                },
                "overnight_runtime": {
                    "action_status": "already_running_monitor_only",
                    "first_path_verdict": "first_path_opened_waiting_close",
                },
                "acceptance": {
                    "verdict": "shadow_ready",
                    "warning_checks": [
                        "early_green_monetization",
                        "forward_proof_integrity",
                    ],
                },
                "shared_score": {
                    "comparison_verdict": "no_adaptive_score",
                    "adaptive_basis": "missing",
                    "adaptive_first_path_verdict": "first_path_opened_waiting_close",
                },
            },
            study_board={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "incumbent_lane": "live_rearm_941777",
                        "incumbent_seat_verdict": "defended_but_contested_live_seat",
                        "adaptive_runtime_status": "hold_launch_packet_defined_not_started",
                    },
                    {
                        "symbol": "USDJPY",
                        "study_status": "adaptive_candidate_without_incumbent",
                        "incumbent_present": False,
                        "adaptive_runtime_status": "hold_disabled_proof_candidate",
                        "adaptive_lane": "shadow_usdjpy_shallow03",
                    },
                ],
            },
            shared_score_board={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "comparison_verdict": "no_adaptive_score",
                    },
                    {
                        "symbol": "USDJPY",
                        "study_status": "adaptive_candidate_without_incumbent",
                        "comparison_verdict": "no_incumbent_score",
                        "adaptive": {"lane": "shadow_usdjpy_shallow03"},
                    },
                ],
            },
            acceptance_board={
                "candidates": [
                    {
                        "candidate_id": "gbpusd_adaptive_comparison_packet",
                        "verdict": "shadow_ready",
                        "warning_checks": [
                            "early_green_monetization",
                            "portfolio_governance",
                            "forward_proof_integrity",
                        ],
                    },
                    {
                        "candidate_id": "usdjpy_bounded_forward_proof",
                        "verdict": "research_only",
                        "candidate_read": "Research-only until fresh bounded proof lands.",
                        "warning_checks": ["forward_proof_integrity"],
                    },
                ]
            },
            overnight_board={
                "rows": [
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                        "action_status": "already_running_monitor_only",
                    },
                    {
                        "packet_id": "usdjpy_bounded_forward_proof",
                        "lane_name": "shadow_usdjpy_gap2",
                        "action_status": "launch_now_manual_packet",
                    },
                ]
            },
        )

        rows = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(payload["summary"]["recommended_verdict"], "cleaner_first_move_but_first_close_pending")
        self.assertIn("first_path_still_open", rows["GBPUSD"]["blind_spot_ids"])
        self.assertNotIn("launch_not_started", rows["GBPUSD"]["blind_spot_ids"])
        self.assertEqual(rows["GBPUSD"]["runtime_action_status"], "already_running_monitor_only")
        self.assertEqual(rows["GBPUSD"]["adaptive_runtime_status"], "already_running_monitor_only")


if __name__ == "__main__":
    unittest.main()
