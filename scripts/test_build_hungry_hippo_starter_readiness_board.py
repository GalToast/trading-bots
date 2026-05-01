#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_starter_readiness_board as board


class BuildHungryHippoStarterReadinessBoardTests(unittest.TestCase):
    def test_build_payload_separates_starter_from_proof_and_launch_followthrough(self) -> None:
        payload = board.build_payload(
            {
                "summary": {
                    "current_unlocked_slot_count": 0,
                    "growth_ladder_symbols": ["USDCAD", "XRPUSD", "AUDUSD"],
                    "lead_symbol": "USDCAD",
                    "proof_lead_symbol": "US30",
                    "proof_lead_estimated_min_lot_margin_usd": 242.57,
                },
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "asset_class": "fx",
                        "current_status": "blocked_waiting_forward_shadow_proof",
                        "blocker_reason": "USDCAD still needs a fresh forward shadow sample.",
                        "machine_truth": {"estimated_min_lot_margin_usd": 2.00},
                    },
                    {
                        "symbol": "XRPUSD",
                        "asset_class": "crypto",
                        "current_status": "blocked_missing_launch_contract_followthrough",
                        "blocker_reason": "Policy exists for XRPUSD, but deployment and runnable launch-contract coverage are still incomplete.",
                        "machine_truth": {"estimated_min_lot_margin_usd": 14.07},
                    },
                    {
                        "symbol": "AUDUSD",
                        "asset_class": "fx",
                        "current_status": "blocked_missing_launch_contract_followthrough",
                        "blocker_reason": "Policy exists for AUDUSD, but deployment and runnable launch-contract coverage are still incomplete.",
                        "machine_truth": {"estimated_min_lot_margin_usd": 1.44},
                    },
                ],
            },
            {
                "rows": [
                    {
                        "action": "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout",
                        "machine_truth": {"policy_seed_now_single_asset_class": True},
                    }
                ]
            },
            {
                "summary": {
                    "family_portable_count": 19,
                    "surface_coverage_complete_count": 8,
                    "waiting_forward_proof_symbols": ["US30"],
                    "ready_for_shadow_discussion_symbols": ["USDCAD"],
                    "status_counts": {"portable_missing_policy": 3},
                },
                "rows": [
                    {
                        "symbol": "US30",
                        "asset_class": "index",
                        "generalization_status": "portable_waiting_forward_proof",
                        "deployment_verdict": "hard_block",
                        "guardrail_status": "promotable_now",
                        "highest_leverage_gap": "fresh_forward_proof",
                        "launch_contract_count": 2,
                    },
                    {
                        "symbol": "XRPUSD",
                        "asset_class": "crypto",
                        "generalization_status": "portable_missing_launch_contract",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "guardrail_status": "promotable_now",
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                    },
                    {
                        "symbol": "AUDUSD",
                        "asset_class": "fx",
                        "generalization_status": "portable_missing_launch_contract",
                        "deployment_verdict": "missing",
                        "guardrail_status": "promotable_now",
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                    },
                    {
                        "symbol": "USDCAD",
                        "asset_class": "fx",
                        "generalization_status": "ready_for_shadow_discussion",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "guardrail_status": "promotable_now",
                        "surface_coverage_complete": True,
                        "launch_contract_count": 1,
                        "highest_leverage_gap": "fresh_shadow_proof",
                    },
                    {
                        "symbol": "USDJPY",
                        "asset_class": "fx",
                        "generalization_status": "portable_missing_launch_contract",
                        "deployment_verdict": "manual_review",
                        "guardrail_status": "promotable_now",
                        "manual_review_reasons": ["atr_manual_review"],
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                    },
                ],
            },
            {
                "summary": {
                    "policy_seed_now_symbols": [],
                    "policy_seed_next_symbols": [],
                }
            },
            {
                "rows": [
                    {
                        "symbol": "XRPUSD",
                        "asset_class": "crypto",
                        "priority": "policy_seed_now",
                        "priority_score": 70,
                        "suggested_seed_action": "seed_regime_selector_and_rearm_bundle",
                        "family_default_timeframe": "M15",
                        "family_default_base_step": 5.0,
                        "evidence_net_usd": 162.38,
                        "evidence_closes": 25,
                    },
                    {
                        "symbol": "AUDUSD",
                        "asset_class": "fx",
                        "priority": "policy_seed_next",
                        "priority_score": 31,
                        "suggested_seed_action": "reconcile_existing_policy_surfaces",
                        "family_default_timeframe": "M15",
                        "family_default_base_step": 0.0004,
                        "evidence_net_usd": 2614.45,
                        "evidence_closes": 2217,
                    },
                ]
            },
            {
                "best_any_symbol_contribution": "add_checked_in_launch_contracts_for_promotable_portable_symbols",
                "research_areas": [
                    {
                        "area": "any_symbol_portability_followthrough_gap",
                        "machine_truth": {
                            "promotable_missing_launch_contract_symbols": ["XRPUSD"],
                            "manual_review_missing_launch_contract_symbols": ["USDJPY"],
                        },
                    }
                ],
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["starter_candidate_symbol"], "USDCAD")
        self.assertEqual(summary["proof_lead_symbol"], "US30")
        self.assertFalse(summary["starter_and_proof_are_same_symbol"])
        self.assertEqual(summary["starter_next_symbol"], "XRPUSD")
        self.assertEqual(summary["starter_policy_debt_symbol"], "")
        self.assertEqual(summary["starter_policy_next_symbol"], "")
        self.assertEqual(summary["cheap_promotable_launch_contract_symbols"], ["XRPUSD"])
        self.assertEqual(summary["manual_review_launch_contract_symbols"], ["USDJPY"])
        self.assertEqual(summary["ready_for_shadow_discussion_nonstarter_symbols"], [])
        self.assertIn("Current starter policy debt is cleared", payload["leadership_read"][1])
        self.assertIn("Cheap promotable launch-contract debt is `['XRPUSD']`", payload["leadership_read"][2])

        self.assertEqual(payload["rows"][0]["lane"], "starter_candidate")
        self.assertEqual(payload["rows"][0]["symbol"], "USDCAD")
        self.assertEqual(payload["rows"][1]["lane"], "proof_lead")
        self.assertEqual(payload["rows"][1]["machine_truth"]["estimated_min_lot_margin_usd"], 242.57)
        self.assertEqual(payload["rows"][2]["lane"], "starter_next_queue")
        self.assertEqual(payload["rows"][2]["symbol"], "XRPUSD")
        self.assertEqual(payload["rows"][2]["next_honest_move"], "shadow_or_live_launch_contract")
        self.assertTrue(payload["rows"][2]["machine_truth"]["launch_contract_followthrough"])
        self.assertEqual(payload["rows"][2]["machine_truth"]["priority"], "")
        self.assertEqual(payload["rows"][3]["lane"], "launch_contract_manual_review")

    def test_render_markdown_mentions_starter_and_proof_lead(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["a"],
                "summary": {
                    "starter_candidate_symbol": "XRPUSD",
                    "starter_candidate_status": "blocked_small_account_starter_missing_policy",
                    "starter_next_symbol": "AUDUSD",
                    "proof_lead_symbol": "NAS100",
                    "proof_lead_status": "portable_waiting_forward_proof",
                    "cheap_promotable_launch_contract_symbols": [],
                    "manual_review_launch_contract_symbols": ["USDJPY"],
                    "ready_for_shadow_discussion_nonstarter_symbols": ["USDCAD", "USDCHF"],
                    "best_any_symbol_contribution": "expand_canonical_policy_coverage_for_portable_missing_policy_symbols",
                },
                "rows": [
                    {
                        "lane": "starter_policy_debt",
                        "symbol": "XRPUSD",
                        "asset_class": "crypto",
                        "current_status": "blocked_small_account_starter_missing_policy",
                        "blocker_reason": "policy",
                        "next_honest_move": "seed_regime_selector_and_rearm_bundle",
                        "why_this_lane": "starter",
                        "machine_truth": {"generalization_status": "portable_missing_policy"},
                    }
                ],
            }
        )

        self.assertIn("Hungry Hippo Starter Readiness Board", markdown)
        self.assertIn("Starter candidate: `XRPUSD`", markdown)
        self.assertIn("Proof lead: `NAS100`", markdown)
        self.assertIn("starter_policy_debt: XRPUSD", markdown)


if __name__ == "__main__":
    unittest.main()
