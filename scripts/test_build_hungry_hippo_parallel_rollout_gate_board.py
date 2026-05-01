#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_parallel_rollout_gate_board as board


class BuildHungryHippoParallelRolloutGateBoardTests(unittest.TestCase):
    def test_build_payload_turns_slot_truth_into_parallel_gates(self) -> None:
        payload = board.build_payload(
            {
                "summary": {
                    "growth_ladder_symbols": ["USDCAD", "XRPUSD", "AUDUSD"],
                    "drawdown_freeze_pct": 0.05,
                    "drawdown_reduce_pct": 0.08,
                    "drawdown_block_pct": 0.10,
                    "max_symbol_risk_pct_of_equity": 0.04,
                    "max_portfolio_risk_pct": 0.10,
                },
                "rows": [
                    {"symbol": "USDCAD", "current_status": "blocked_waiting_forward_shadow_proof", "blocker_reason": "starter proof"},
                    {"symbol": "XRPUSD", "current_status": "blocked_missing_launch_contract_followthrough", "blocker_reason": "slot2 contract"},
                    {"symbol": "AUDUSD", "current_status": "blocked_policy_reconciliation", "blocker_reason": "slot3 policy"},
                ],
            },
            {
                "summary": {
                    "starter_candidate_symbol": "USDCAD",
                    "starter_candidate_status": "blocked_waiting_forward_shadow_proof",
                    "starter_next_symbol": "XRPUSD",
                    "starter_next_status": "blocked_missing_launch_contract_followthrough",
                    "proof_lead_symbol": "US30",
                    "proof_lead_status": "portable_waiting_forward_proof",
                    "cheap_promotable_launch_contract_symbols": ["XRPUSD"],
                    "manual_review_launch_contract_symbols": ["USDJPY"],
                    "policy_seed_now_symbols": [],
                },
                "rows": [
                    {
                        "lane": "starter_candidate",
                        "symbol": "USDCAD",
                        "current_status": "blocked_waiting_forward_shadow_proof",
                        "blocker_reason": "starter proof",
                        "next_honest_move": "fresh_forward_proof",
                        "machine_truth": {"estimated_min_lot_margin_usd": 2.0},
                    },
                    {
                        "lane": "starter_next_queue",
                        "symbol": "XRPUSD",
                        "current_status": "blocked_missing_launch_contract_followthrough",
                        "blocker_reason": "slot2 contract",
                        "next_honest_move": "shadow_or_live_launch_contract",
                        "machine_truth": {"estimated_min_lot_margin_usd": 14.08},
                    },
                ],
            },
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "generalization_status": "ready_for_shadow_discussion",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "guardrail_status": "promotable_now",
                        "highest_leverage_gap": "fresh_forward_proof",
                    },
                    {
                        "symbol": "XRPUSD",
                        "generalization_status": "portable_missing_launch_contract",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "guardrail_status": "promotable_now",
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                    },
                    {
                        "symbol": "AUDUSD",
                        "generalization_status": "portable_missing_launch_contract",
                        "deployment_verdict": "missing",
                        "guardrail_status": "promotable_now",
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                    },
                ]
            },
            {
                "rows": [
                    {
                        "action": "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout",
                        "machine_truth": {"policy_seed_now_symbols": []},
                    }
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["current_max_honest_active_lanes"], 0)
        self.assertEqual(summary["starter_candidate_symbol"], "USDCAD")
        self.assertEqual(summary["starter_next_symbol"], "XRPUSD")
        self.assertEqual(summary["slot3_symbol"], "AUDUSD")
        self.assertTrue(summary["slot3_surface_disagreement"])
        self.assertEqual(summary["promotable_missing_launch_contract_symbols"], ["XRPUSD"])
        self.assertIn("Cheap FX margin is not permission", payload["leadership_read"][0])
        self.assertIn("mixed upstream truth", payload["leadership_read"][2])

        self.assertEqual(payload["rows"][0]["max_active_lanes"], 1)
        self.assertEqual(payload["rows"][1]["machine_truth"]["slot2_generalization_status"], "portable_missing_launch_contract")
        self.assertEqual(payload["rows"][2]["machine_truth"]["slot3_portability_status"], "portable_missing_launch_contract")

    def test_render_markdown_mentions_parallel_gate(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["a"],
                "summary": {
                    "current_max_honest_active_lanes": 0,
                    "starter_candidate_symbol": "USDCAD",
                    "starter_candidate_status": "blocked_waiting_forward_shadow_proof",
                    "starter_next_symbol": "XRPUSD",
                    "starter_next_status": "blocked_missing_launch_contract_followthrough",
                    "slot3_symbol": "AUDUSD",
                    "slot3_surface_disagreement": True,
                    "parallel_rollout_doctrine": "cheap_margin_is_not_permission_until_slot1_proves_and_slot2_is_real",
                },
                "rows": [
                    {
                        "max_active_lanes": 1,
                        "current_status": "blocked_until_slot1_forward_proof",
                        "blocker_reason": "proof",
                        "unlock_when": "u",
                        "kill_when": "k",
                        "machine_truth": {"starter_candidate_symbol": "USDCAD"},
                    }
                ],
            }
        )

        self.assertIn("Hungry Hippo Parallel Rollout Gate Board", markdown)
        self.assertIn("Current max honest active lanes: `0`", markdown)
        self.assertIn("Max Active Lanes = 1", markdown)


if __name__ == "__main__":
    unittest.main()
