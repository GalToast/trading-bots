from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_execution_ready_control_contract_board as board


class BuildBtcExecutionReadyControlContractBoardTests(unittest.TestCase):
    def test_build_payload_compresses_btc_preparatory_truth(self) -> None:
        payload = board.build_payload(
            seat_board={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "seat_execution_gate_status": "queue_backed_preparatory_only",
                        "seat_execution_gate_read": "This symbol already has queue coverage, but the queue contract is still preparatory relative to the current seat call.",
                        "seat_queue_alignment_status": "queue_ready_precedes_seat_call",
                        "seat_overlay_contract_status": "preparatory_overlay_contract",
                        "seat_overlay_contract_read": "Overlay contract still active.",
                        "seat_overlay_launch_bridge_status": "overlay_launch_bridge_supported_but_unrequested",
                        "seat_overlay_launch_bridge_read": "Runner support exists, but the current plan still does not request those overlays.",
                        "seat_unblocker_queue_task_id": "btc_restore_comparison_shadow",
                    }
                ]
            },
            adaptive_queue={
                "tasks": [
                    {
                        "task_id": "btc_restore_comparison_shadow",
                        "title": "Launch the BTC M15 warp restore comparison shadow",
                        "status": "ready",
                        "lane": "shadow crypto",
                        "priority": 1,
                        "profit_mode": "guarded_toxic_flow",
                        "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                        "runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                        "runtime_overlay_read": "Guard new opens and collapse burst fills into one risk unit.",
                        "runtime_overlays": [
                            "guard_open_admission",
                            "cluster_aware_escape",
                            "suppress_additional_levels_after_burst",
                        ],
                    }
                ]
            },
            branch_board={
                "summary": {
                    "recommended_branch_id": "launch_restore_comparison_shadow",
                    "doctrine_target_branch_id": "define_true_adaptive_candidate_then_build",
                },
                "rows": [
                    {
                        "branch_id": "launch_restore_comparison_shadow",
                        "title": "Launch the BTC M15 warp restore comparison shadow",
                        "acceptance_verdict": "shadow_ready",
                        "launch_status": "already_running_monitor_only",
                        "why": "Keep the live baseline intact while gathering fresh BTC control proof.",
                    },
                    {
                        "branch_id": "define_true_adaptive_candidate_then_build",
                        "title": "Define and build the true downtrend-aware adaptive BTC candidate",
                        "acceptance_verdict": "research_only",
                    },
                ],
            },
            guarded_contract={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "contract": {
                            "verdict": "cluster_escape_primary_spread_demoted",
                            "read": "Burst context first, spread second, cluster escape primary.",
                            "primary_entry_guard": "same_bar_open_burst_count_at_open + regime_at_entry",
                            "escape_role": "cluster_aware_escape_when_burst_clusters_form",
                            "step_widening_role": "secondary_hypothesis_until_checked_in_support",
                        },
                        "runtime_evidence": {
                            "verdict": "runtime_visibility_missing",
                            "read": "Current artifact does not expose guard_open_admission or open_guarded_admission.",
                        },
                    }
                ]
            },
            next_action_board={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "max_profit_posture": "preparatory_only",
                        "queue_task_id": "btc_restore_comparison_shadow",
                        "queue_task_status": "ready",
                        "queue_task_title": "Launch the BTC M15 warp restore comparison shadow",
                        "queue_lane": "shadow crypto",
                        "profit_mode": "guarded_toxic_flow",
                        "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                        "launch_read": "BTC stays preparatory until the overlay contract and guarded runtime evidence are real.",
                    }
                ]
            },
            runner_plan={
                "runtime_overlay_contract": {
                    "supported_overlays": [
                        "guard_open_admission",
                        "cluster_aware_escape",
                        "suppress_additional_levels_after_burst",
                    ],
                    "requested_overlays": [],
                    "executable_overlays": [],
                    "unsupported_overlays": [],
                    "read": "The scaffold can express the full guarded-toxic-flow overlay set when requested.",
                }
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["contract_status"], "preparatory_control_contract")
        self.assertEqual(summary["seat_execution_gate_status"], "queue_backed_preparatory_only")
        self.assertEqual(summary["queue_task_id"], "btc_restore_comparison_shadow")
        self.assertEqual(summary["recommended_branch_id"], "launch_restore_comparison_shadow")
        self.assertEqual(summary["runtime_obligation_class"], "prove_guarded_open_admission_with_cluster_escape")
        self.assertEqual(summary["seat_overlay_launch_bridge_status"], "overlay_launch_bridge_supported_but_unrequested")
        self.assertEqual(summary["runtime_visibility_verdict"], "runtime_visibility_missing")
        self.assertIn("seat_execution_gate", summary["graduation_blocker_ids"])
        self.assertIn("overlay_launch_alignment", summary["graduation_blocker_ids"])
        self.assertIn("guarded_runtime_visibility", summary["graduation_blocker_ids"])

        control_branch = payload["control_branch"]
        self.assertEqual(control_branch["queue_task_status"], "ready")
        self.assertEqual(control_branch["recommended_branch_acceptance_verdict"], "shadow_ready")
        self.assertEqual(control_branch["runtime_overlays"][0], "guard_open_admission")

        overlay_truth = payload["overlay_truth"]
        self.assertEqual(overlay_truth["supported_overlays"], ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"])
        self.assertEqual(overlay_truth["requested_overlays"], [])

    def test_render_markdown_mentions_contract_and_inference(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "symbol": "BTCUSD",
                    "contract_status": "preparatory_control_contract",
                    "seat_execution_gate_status": "queue_backed_preparatory_only",
                    "seat_queue_alignment_status": "queue_ready_precedes_seat_call",
                    "max_profit_posture": "preparatory_only",
                    "queue_task_id": "btc_restore_comparison_shadow",
                    "queue_task_status": "ready",
                    "recommended_branch_id": "launch_restore_comparison_shadow",
                    "recommended_branch_acceptance_verdict": "shadow_ready",
                    "recommended_branch_launch_status": "already_running_monitor_only",
                    "doctrine_target_branch_id": "define_true_adaptive_candidate_then_build",
                    "doctrine_target_acceptance_verdict": "research_only",
                    "runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                    "guarded_contract_verdict": "cluster_escape_primary_spread_demoted",
                    "runtime_visibility_verdict": "runtime_visibility_missing",
                    "seat_overlay_launch_bridge_status": "overlay_launch_bridge_supported_but_unrequested",
                    "graduation_blocker_ids": ["seat_execution_gate", "overlay_launch_alignment"],
                    "contract_read": "Inference from checked-in statuses.",
                },
                "leadership_read": ["BTC remains preparatory."],
                "control_branch": {
                    "queue_task_id": "btc_restore_comparison_shadow",
                    "queue_task_status": "ready",
                    "queue_task_lane": "shadow crypto",
                    "queue_task_title": "Launch the BTC M15 warp restore comparison shadow",
                    "profit_mode": "guarded_toxic_flow",
                    "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                    "runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                    "runtime_overlays": ["guard_open_admission"],
                    "runtime_overlay_read": "guard read",
                    "recommended_branch_id": "launch_restore_comparison_shadow",
                    "recommended_branch_acceptance_verdict": "shadow_ready",
                    "recommended_branch_launch_status": "already_running_monitor_only",
                    "recommended_branch_read": "branch read",
                },
                "doctrine_boundary": {
                    "recommended_branch_id": "launch_restore_comparison_shadow",
                    "doctrine_target_branch_id": "define_true_adaptive_candidate_then_build",
                    "recommended_branch_acceptance_verdict": "shadow_ready",
                    "doctrine_target_acceptance_verdict": "research_only",
                    "read": "boundary read",
                },
                "overlay_truth": {
                    "seat_overlay_contract_status": "preparatory_overlay_contract",
                    "seat_overlay_contract_read": "overlay contract read",
                    "seat_overlay_launch_bridge_status": "overlay_launch_bridge_supported_but_unrequested",
                    "seat_overlay_launch_bridge_read": "overlay launch read",
                    "supported_overlays": ["guard_open_admission"],
                    "requested_overlays": [],
                    "executable_overlays": [],
                    "unsupported_overlays": [],
                    "runner_plan_read": "runner read",
                },
                "guarded_contract_truth": {
                    "contract_verdict": "cluster_escape_primary_spread_demoted",
                    "contract_read": "guarded read",
                    "primary_entry_guard": "guard",
                    "escape_role": "escape",
                    "step_widening_role": "step",
                    "runtime_visibility_verdict": "runtime_visibility_missing",
                    "runtime_visibility_read": "runtime read",
                },
                "graduation_blockers": [
                    {
                        "blocker_id": "overlay_launch_alignment",
                        "status": "overlay_launch_bridge_supported_but_unrequested",
                        "read": "alignment read",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("BTC Execution-Ready Control Contract Board", markdown)
        self.assertIn("preparatory_control_contract", markdown)
        self.assertIn("overlay_launch_bridge_supported_but_unrequested", markdown)
        self.assertIn("Inference from current checked-in statuses", markdown)
        self.assertIn("cluster_escape_primary_spread_demoted", markdown)


if __name__ == "__main__":
    unittest.main()
