#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_btc_branch_decision_board as board


class BuildAdaptiveBTCBranchDecisionBoardTests(unittest.TestCase):
    def test_build_payload_separates_restore_from_true_adaptive_branch(self) -> None:
        payload = board.build_payload(
            restore_board={
                "restore_candidate": {
                    "verdict": "launch_shadow_restore_comparison",
                    "action": "DO NOT retune live lane. Launch new shadow with optimal geometry for comparison.",
                    "lane": "shadow_btcusd_m15_warp_restore_v1",
                }
            },
            runtime_audit={
                "status": "runtime_present_manual_review_required",
                "lane_name": "shadow_btcusd_m15_adaptive_regime",
                "summary": {
                    "completion_read": "Treat the parked direct-live lane as historical runtime evidence only."
                },
                "runtime_objective_context": {
                    "close_conversion_pressure": True,
                    "objective_read": "Monetization pressure active.",
                },
                "runtime_lane": {"lane_name": "shadow_btcusd_m15_adaptive_regime"},
            },
            downtrend_handoff={
                "summary": {
                    "completion_read": "Either hold the hybrid bullish runtime in review, or build a dedicated downtrend-aware BTC candidate."
                },
                "proposed_downtrend_shape": {"shape_id": "btcusd_m15_bounce_down_v1"},
            },
            adaptive_plan={
                "status": "ready",
                "warnings": [],
                "controller_recommendation": {
                    "recommended_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                },
                "step_review": {
                    "review_read": "Judge the adaptive step against the unified design target first.",
                    "notes": ["legacy_warp_baseline_separation_expected:31.01x"],
                },
            },
            overnight_packet={
                "rows": [
                    {
                        "packet_id": "btc_restore_comparison_shadow",
                        "action_status": "already_running_monitor_only",
                        "action_read": "restore packet already started",
                    },
                    {
                        "packet_id": "btc_parked_adaptive_artifact",
                        "action_status": "hold_parked_artifact",
                        "action_read": "historical context only",
                    },
                ]
            },
            acceptance_verdict={
                "candidates": [
                    {
                        "candidate_id": "btc_restore_comparison_shadow",
                        "verdict": "shadow_ready",
                        "candidate_read": "launch-ready control branch",
                    },
                    {
                        "candidate_id": "btc_parked_artifact_review",
                        "verdict": "rejected",
                        "candidate_read": "stale context only",
                    },
                    {
                        "candidate_id": "btc_true_adaptive_candidate",
                        "verdict": "research_only",
                        "candidate_read": "explicit but still proof-thin",
                    },
                ]
            },
        )

        rows = {row["branch_id"]: row for row in payload["rows"]}
        self.assertEqual(payload["summary"]["recommended_branch_id"], "launch_restore_comparison_shadow")
        self.assertEqual(payload["summary"]["doctrine_target_branch_id"], "define_true_adaptive_candidate_then_build")
        self.assertEqual(payload["summary"]["recommended_branch_launch_status"], "already_running_monitor_only")
        self.assertEqual(payload["summary"]["recommended_branch_acceptance_verdict"], "shadow_ready")
        self.assertEqual(payload["summary"]["doctrine_target_acceptance_verdict"], "research_only")
        self.assertEqual(payload["summary"]["adaptive_plan_shape_id"], "btcusd_rangeatr_cash_harvest_v1")
        self.assertTrue(payload["summary"]["adaptive_plan_close_conversion_pressure"])
        self.assertEqual(rows["launch_restore_comparison_shadow"]["status"], "recommended_next_action")
        self.assertEqual(rows["launch_restore_comparison_shadow"]["launch_status"], "already_running_monitor_only")
        self.assertEqual(rows["launch_restore_comparison_shadow"]["acceptance_verdict"], "shadow_ready")
        self.assertEqual(rows["define_true_adaptive_candidate_then_build"]["status"], "doctrine_target_not_first_build")
        self.assertEqual(rows["define_true_adaptive_candidate_then_build"]["acceptance_verdict"], "research_only")
        self.assertEqual(rows["define_true_adaptive_candidate_then_build"]["execution_read"], "monetization_aware_shadow_candidate")
        self.assertIn("btcusd_rangeatr_cash_harvest_v1", rows["define_true_adaptive_candidate_then_build"]["why"])
        self.assertIn("Judge the adaptive step", rows["define_true_adaptive_candidate_then_build"]["review_read"])
        self.assertIn("legacy_warp_baseline_separation_expected", rows["define_true_adaptive_candidate_then_build"]["review_notes"][0])
        self.assertEqual(rows["hold_parked_artifact_only"]["status"], "not_next_action")
        self.assertEqual(rows["hold_parked_artifact_only"]["acceptance_verdict"], "rejected")

    def test_render_markdown_mentions_recommended_and_doctrine_target_branches(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T05:00:00+00:00",
                "summary": {
                    "branch_count": 3,
                    "recommended_branch_id": "launch_restore_comparison_shadow",
                    "doctrine_target_branch_id": "define_true_adaptive_candidate_then_build",
                    "parked_runtime_status": "runtime_present_manual_review_required",
                    "restore_candidate_verdict": "launch_shadow_restore_comparison",
                    "adaptive_plan_status": "manual_review_required",
                    "adaptive_plan_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                    "adaptive_plan_close_conversion_pressure": True,
                    "recommended_branch_launch_status": "already_running_monitor_only",
                    "recommended_branch_acceptance_verdict": "shadow_ready",
                    "doctrine_target_acceptance_verdict": "research_only",
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "branch_id": "launch_restore_comparison_shadow",
                        "title": "Launch restore comparison",
                        "status": "recommended_next_action",
                        "doctrine_alignment": "medium",
                        "execution_read": "explicit_shadow_packet_ready",
                        "launch_status": "already_running_monitor_only",
                        "launch_read": "restore packet already started",
                        "acceptance_verdict": "shadow_ready",
                        "acceptance_read": "launch-ready control branch",
                        "why": "why",
                        "allowed_inputs": ["shadow_btcusd_m15_warp_restore_v1"],
                        "review_read": "",
                        "review_notes": [],
                        "blockers": [],
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive BTC Branch Decision Board", markdown)
        self.assertIn("recommended_branch_id", markdown)
        self.assertIn("launch_restore_comparison_shadow", markdown)
        self.assertIn("define_true_adaptive_candidate_then_build", markdown)
        self.assertIn("recommended_branch_launch_status", markdown)
        self.assertIn("adaptive_plan_shape_id", markdown)
        self.assertIn("shadow_ready", markdown)


if __name__ == "__main__":
    unittest.main()
