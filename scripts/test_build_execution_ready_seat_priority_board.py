from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_execution_ready_seat_priority_board as board


class BuildExecutionReadySeatPriorityBoardTests(unittest.TestCase):
    def test_build_payload_recommends_gbpusd_first(self) -> None:
        payload = board.build_payload(
            seat_board={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "current_live_holder_lane": "live_rearm_941777",
                        "seat_conflict": True,
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "GBP seat move is execution-ready.",
                        "seat_unblocker_action": "complete_challenger_comparison",
                        "seat_unblocker_read": "Finish challenger comparison.",
                        "seat_unblocker_queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "seat_unblocker_queue_task_title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                        "seat_unblocker_queue_task_status": "ready",
                        "seat_unblocker_queue_task_lane": "shadow FX",
                        "seat_unblocker_queue_task_next_action_class": "shadow_compare_and_score",
                        "best_challenger_candidate_class": "shadow_ready",
                        "best_challenger_runtime_status": "hold_launch_packet_defined_not_started",
                        "best_challenger_objective_status": "challenger_partially_comparable",
                        "max_profit_objective_status": "profitable_but_contested_reference",
                    },
                    {
                        "symbol": "USDJPY",
                        "seat_verdict": "no_live_seat",
                        "current_live_holder_lane": "",
                        "seat_conflict": False,
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "USDJPY seat move is execution-ready.",
                        "seat_unblocker_action": "collect_first_comparable_proof",
                        "seat_unblocker_read": "Collect first comparable proof.",
                        "seat_unblocker_queue_task_id": "usdjpy_bounded_forward_proof",
                        "seat_unblocker_queue_task_title": "Run fresh USDJPY bounded forward proof under the restored friction-survivor branch",
                        "seat_unblocker_queue_task_status": "ready",
                        "seat_unblocker_queue_task_lane": "shadow FX",
                        "seat_unblocker_queue_task_next_action_class": "prove_executability_and_survival_before_promotion",
                        "best_challenger_candidate_class": "research_only",
                        "best_challenger_runtime_status": "hold_disabled_proof_candidate",
                        "best_challenger_objective_status": "challenger_partially_comparable",
                        "max_profit_objective_status": "missing_live_seat",
                    },
                ]
            },
            adaptive_queue={
                "tasks": [
                    {
                        "task_id": "gbpusd_adaptive_comparison_packet",
                        "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                        "status": "ready",
                        "priority": 4,
                        "lane": "shadow FX",
                        "next_action_class": "shadow_compare_and_score",
                        "profit_mode": "trend_harvest",
                    },
                    {
                        "task_id": "usdjpy_bounded_forward_proof",
                        "title": "Run fresh USDJPY bounded forward proof under the restored friction-survivor branch",
                        "status": "ready",
                        "priority": 6,
                        "lane": "shadow FX",
                        "next_action_class": "prove_executability_and_survival_before_promotion",
                        "profit_mode": "friction_survivor",
                    },
                ]
            },
            gbp_first_path={
                "summary": {
                    "adaptive_lane": "shadow_gbpusd_m15_trend_harvest_v1",
                    "proof_gate_status": "packet_defined_waiting_launch",
                    "runtime_truth_source_status": "watcher_state_fresh",
                    "overnight_action_status": "hold_launch_packet_defined_not_started",
                    "first_path_verdict": "awaiting_first_trade_path_event",
                }
            },
            overnight_board={
                "rows": [
                    {
                        "packet_id": "usdjpy_bounded_forward_proof",
                        "lane_name": "shadow_usdjpy_gap2",
                        "action_status": "launch_now_manual_packet",
                        "action_read": "bounded proof relaunch packet is now explicit and ready for manual relaunch",
                        "first_path_verdict": "",
                    }
                ]
            },
            shared_score={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "comparison_verdict": "no_adaptive_score",
                        "shared_score_ready": False,
                        "adaptive": {"basis": "missing"},
                    },
                    {
                        "symbol": "USDJPY",
                        "comparison_verdict": "no_incumbent_score",
                        "shared_score_ready": False,
                        "adaptive": {"basis": "booked_usd_proxy"},
                    },
                ]
            },
            acceptance_board={
                "candidates": [
                    {
                        "candidate_id": "gbpusd_adaptive_comparison_packet",
                        "verdict": "shadow_ready",
                        "candidate_read": "GBP comparison packet is shadow-ready.",
                        "checks": [{"check_id": "forward_proof_integrity", "status": "warn", "read": "Fresh proof still missing."}],
                    },
                    {
                        "candidate_id": "usdjpy_bounded_forward_proof",
                        "verdict": "research_only",
                        "candidate_read": "USDJPY still needs fresh bounded runtime proof.",
                        "checks": [{"check_id": "forward_proof_integrity", "status": "warn", "read": "Fresh bounded sample still missing."}],
                    },
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["recommended_symbol"], "GBPUSD")
        self.assertEqual(summary["recommended_option"], "advance_gbpusd_first")
        self.assertEqual(summary["deferred_symbol"], "USDJPY")
        self.assertEqual(summary["execution_ready_symbols"], ["GBPUSD", "USDJPY"])

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["GBPUSD"]["seat_case_type"], "incumbent_comparison_seam")
        self.assertEqual(indexed["GBPUSD"]["acceptance_verdict"], "shadow_ready")
        self.assertEqual(indexed["GBPUSD"]["proof_contract_status"], "packet_defined_waiting_launch")
        self.assertEqual(indexed["USDJPY"]["seat_case_type"], "first_seat_construction_seam")
        self.assertEqual(indexed["USDJPY"]["acceptance_verdict"], "research_only")
        self.assertEqual(indexed["USDJPY"]["proof_contract_status"], "launch_packet_ready_waiting_runtime")

    def test_render_markdown_mentions_recommendation(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "decision_id": 12,
                    "compared_symbols": ["GBPUSD", "USDJPY"],
                    "execution_ready_symbols": ["GBPUSD", "USDJPY"],
                    "recommended_symbol": "GBPUSD",
                    "recommended_option": "advance_gbpusd_first",
                    "deferred_symbol": "USDJPY",
                    "parallel_feasible": True,
                    "decision_read": "Recommend GBPUSD first.",
                },
                "leadership_read": ["Both are execution-ready."],
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "recommendation_option": "advance_gbpusd_first",
                        "recommendation_read": "Recommend GBPUSD first.",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "seat_case_type": "incumbent_comparison_seam",
                        "seat_case_read": "seat case read",
                        "current_live_holder_lane": "live_rearm_941777",
                        "seat_conflict": True,
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "gate read",
                        "seat_unblocker_action": "complete_challenger_comparison",
                        "seat_unblocker_read": "unblocker read",
                        "queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "queue_task_title": "queue title",
                        "queue_status": "ready",
                        "queue_priority": 4,
                        "queue_lane": "shadow FX",
                        "next_action_class": "shadow_compare_and_score",
                        "profit_mode": "trend_harvest",
                        "acceptance_verdict": "shadow_ready",
                        "acceptance_read": "acceptance read",
                        "acceptance_warning_checks": ["forward_proof_integrity"],
                        "runtime_source": "reports/gbpusd_adaptive_first_path_board.json",
                        "runtime_source_status": "watcher_state_fresh",
                        "runtime_lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                        "proof_contract_status": "packet_defined_waiting_launch",
                        "runtime_action_status": "hold_launch_packet_defined_not_started",
                        "first_path_verdict": "awaiting_first_trade_path_event",
                        "proof_read": "proof read",
                        "shared_score_verdict": "no_adaptive_score",
                        "shared_score_ready": False,
                        "shared_adaptive_basis": "missing",
                        "best_challenger_candidate_class": "shadow_ready",
                        "best_challenger_runtime_status": "hold_launch_packet_defined_not_started",
                        "best_challenger_objective_status": "challenger_partially_comparable",
                        "max_profit_objective_status": "profitable_but_contested_reference",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Execution-Ready Seat Priority Board", markdown)
        self.assertIn("advance_gbpusd_first", markdown)
        self.assertIn("incumbent_comparison_seam", markdown)
        self.assertIn("packet_defined_waiting_launch", markdown)


if __name__ == "__main__":
    unittest.main()
