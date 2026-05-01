from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_next_action_board as board


class BuildMaxProfitNextActionBoardTests(unittest.TestCase):
    def test_build_payload_ranks_launch_and_preparatory_symbols(self) -> None:
        payload = board.build_payload(
            seat_board={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "seat_verdict": "contested_provisional_live_seat",
                        "seat_unblocker_action": "controlled_displacement_review",
                        "seat_actionability_status": "queue_ready_preparatory_only",
                        "seat_contract_gap_status": "queue_backed_preparatory_only",
                        "seat_queue_alignment_status": "queue_ready_precedes_seat_call",
                        "seat_unblocker_queue_task_id": "btc_restore_comparison_shadow",
                        "seat_unblocker_queue_task_title": "Launch the BTC M15 warp restore comparison shadow",
                        "seat_unblocker_queue_task_status": "ready",
                        "seat_unblocker_queue_task_lane": "shadow crypto",
                        "seat_unblocker_queue_task_next_action_class": "control_shadow_and_collect_path_safety_evidence",
                        "seat_execution_gate_status": "queue_backed_preparatory_only",
                        "seat_execution_gate_read": "This symbol already has queue coverage, but the queue contract is still preparatory relative to the current seat call.",
                        "seat_overlay_contract_status": "preparatory_overlay_contract",
                        "seat_overlay_contract_read": "Overlay contract still active.",
                        "seat_overlay_launch_bridge_status": "overlay_launch_bridge_unrequested",
                        "seat_overlay_launch_bridge_read": "Runner plan does not yet request required overlays.",
                        "seat_unblocker_priority_rank": 1,
                    },
                    {
                        "symbol": "GBPUSD",
                        "seat_verdict": "contested_live_seat",
                        "seat_unblocker_action": "complete_challenger_comparison",
                        "seat_actionability_status": "queue_ready_actionable",
                        "seat_contract_gap_status": "queue_backed_actionable",
                        "seat_queue_alignment_status": "queue_ready_aligned",
                        "seat_unblocker_queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "seat_unblocker_queue_task_title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                        "seat_unblocker_queue_task_status": "ready",
                        "seat_unblocker_queue_task_lane": "shadow FX",
                        "seat_unblocker_queue_task_next_action_class": "shadow_compare_and_score",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "seat_execution_gate_read": "This seat move is execution-ready on the current passive evidence, with no additional queue or overlay-launch blocker surfaced here.",
                        "seat_overlay_contract_status": "no_overlay_contract",
                        "seat_overlay_contract_read": "No overlay contract.",
                        "seat_overlay_launch_bridge_status": "no_overlay_launch_bridge_needed",
                        "seat_overlay_launch_bridge_read": "No launch bridge needed.",
                        "seat_unblocker_priority_rank": 4,
                    },
                    {
                        "symbol": "USDCAD",
                        "seat_verdict": "no_live_seat",
                        "seat_unblocker_action": "prepare_first_live_seat_case",
                        "seat_actionability_status": "local_actionable_unqueued",
                        "seat_contract_gap_status": "actionable_missing_queue_contract",
                        "seat_queue_alignment_status": "no_queue_contract",
                        "seat_execution_gate_status": "actionable_but_missing_queue_contract",
                        "seat_execution_gate_read": "This seat move is locally actionable, but it still needs a formal adaptive-lab queue contract before it should be treated as execution-ready.",
                        "queue_task_id": "",
                        "queue_task_status": "",
                        "seat_unblocker_priority_rank": None,
                    },
                    {
                        "symbol": "EURUSD",
                        "seat_verdict": "contested_live_seat",
                        "seat_unblocker_action": "keep_incumbent_collect_challenger_proof",
                        "seat_actionability_status": "blocked_by_queue_contract",
                        "seat_contract_gap_status": "queue_contract_blocked",
                        "seat_queue_alignment_status": "queue_blocked_aligned",
                        "seat_unblocker_queue_task_id": "eurusd_friction_survivor_research",
                        "seat_unblocker_queue_task_title": "Keep EURUSD on friction-survivor research until forward proof beats the incumbent",
                        "seat_unblocker_queue_task_status": "blocked",
                        "seat_unblocker_queue_task_lane": "shadow FX",
                        "seat_unblocker_queue_task_next_action_class": "prove_executability_and_survival_before_promotion",
                        "seat_execution_gate_status": "blocked_by_queue_contract",
                        "seat_execution_gate_read": "This seat move is not executable yet because its queue-backed contract is still blocked.",
                        "seat_unblocker_priority_rank": 10,
                    },
                ]
            },
            adaptive_queue={
                "tasks": [
                    {
                        "task_id": "btc_restore_comparison_shadow",
                        "priority": 1,
                        "status": "ready",
                        "lane": "shadow crypto",
                        "profit_mode": "guarded_toxic_flow",
                        "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                    },
                    {
                        "task_id": "gbpusd_adaptive_comparison_packet",
                        "priority": 4,
                        "status": "ready",
                        "lane": "shadow FX",
                        "profit_mode": "trend_harvest",
                        "next_action_class": "shadow_compare_and_score",
                    },
                    {
                        "task_id": "eurusd_friction_survivor_research",
                        "priority": 10,
                        "status": "blocked",
                        "lane": "shadow FX",
                        "profit_mode": "friction_survivor",
                        "next_action_class": "prove_executability_and_survival_before_promotion",
                    },
                ]
            },
            guarded_contract={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "contract": {
                            "verdict": "cluster_escape_primary_spread_demoted",
                            "read": "BTC guarded contract read",
                        }
                    }
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["highest_launch_now_symbol"], "GBPUSD")
        self.assertEqual(summary["highest_preparatory_symbol"], "BTCUSD")
        self.assertEqual(summary["execution_ready_symbols"], ["GBPUSD"])
        self.assertEqual(summary["queue_contract_missing_symbols"], ["USDCAD"])
        self.assertEqual(summary["blocked_symbols"], ["EURUSD"])

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["GBPUSD"]["max_profit_posture"], "launch_now")
        self.assertEqual(indexed["GBPUSD"]["seat_execution_gate_status"], "ready_for_seat_execution")
        self.assertEqual(indexed["GBPUSD"]["queue_task_id"], "gbpusd_adaptive_comparison_packet")
        self.assertEqual(indexed["GBPUSD"]["queue_lane"], "shadow FX")
        self.assertEqual(indexed["BTCUSD"]["max_profit_posture"], "preparatory_only")
        self.assertEqual(indexed["BTCUSD"]["seat_overlay_launch_bridge_status"], "overlay_launch_bridge_unrequested")
        self.assertEqual(indexed["BTCUSD"]["guarded_contract_verdict"], "cluster_escape_primary_spread_demoted")
        self.assertEqual(indexed["BTCUSD"]["next_action_class"], "control_shadow_and_collect_path_safety_evidence")
        self.assertEqual(indexed["USDCAD"]["max_profit_posture"], "queue_contract_missing")
        self.assertEqual(indexed["EURUSD"]["max_profit_posture"], "blocked")

    def test_render_markdown_mentions_postures(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "launch_now_symbols": ["GBPUSD"],
                    "preparatory_symbols": ["BTCUSD"],
                    "queue_contract_missing_symbols": ["USDCAD"],
                    "blocked_symbols": ["EURUSD"],
                    "execution_ready_symbols": ["GBPUSD"],
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "max_profit_posture": "preparatory_only",
                        "max_profit_posture_read": "prep",
                        "seat_verdict": "contested_provisional_live_seat",
                        "seat_unblocker_action": "controlled_displacement_review",
                        "seat_actionability_status": "queue_ready_preparatory_only",
                        "seat_contract_gap_status": "queue_backed_preparatory_only",
                        "seat_queue_alignment_status": "queue_ready_precedes_seat_call",
                        "seat_execution_gate_status": "queue_backed_preparatory_only",
                        "seat_execution_gate_read": "gate read",
                        "seat_overlay_contract_status": "preparatory_overlay_contract",
                        "seat_overlay_contract_read": "overlay contract read",
                        "seat_overlay_launch_bridge_status": "overlay_launch_bridge_unrequested",
                        "seat_overlay_launch_bridge_read": "overlay bridge read",
                        "queue_task_id": "btc_restore_comparison_shadow",
                        "queue_task_title": "Launch the BTC M15 warp restore comparison shadow",
                        "queue_task_status": "ready",
                        "queue_task_priority": 1,
                        "queue_lane": "shadow crypto",
                        "profit_mode": "guarded_toxic_flow",
                        "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                        "launch_read": "launch read",
                        "guarded_contract_active": True,
                        "guarded_contract_verdict": "cluster_escape_primary_spread_demoted",
                        "guarded_contract_read": "guarded read",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max Profit Next Action Board", markdown)
        self.assertIn("preparatory_only", markdown)
        self.assertIn("execution_ready_symbols", markdown)
        self.assertIn("overlay_launch_bridge_unrequested", markdown)
        self.assertIn("cluster_escape_primary_spread_demoted", markdown)


if __name__ == "__main__":
    unittest.main()
