#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_account_unlock_gate_board as board


class BuildHungryHippoAccountUnlockGateBoardTests(unittest.TestCase):
    def test_build_payload_creates_fx_first_balance_growth_ladder(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "action": "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout",
                        "machine_truth": {"lead_forward_proof_symbol": "NAS100"},
                    }
                ]
            },
            {
                "summary": {"waiting_forward_proof_symbols": ["NAS100"]},
                "rows": [
                    {
                        "symbol": "NAS100",
                        "asset_class": "index",
                        "generalization_status": "portable_waiting_forward_proof",
                        "highest_leverage_gap": "forward_shadow_proof",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "hard_block",
                        "launch_contract_count": 2,
                    },
                    {
                        "symbol": "USDCAD",
                        "asset_class": "fx",
                        "generalization_status": "ready_for_shadow_discussion",
                        "highest_leverage_gap": "fresh_forward_proof",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "launch_contract_count": 1,
                    },
                    {
                        "symbol": "USDCHF",
                        "asset_class": "fx",
                        "generalization_status": "ready_for_shadow_discussion",
                        "highest_leverage_gap": "fresh_forward_proof",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "launch_contract_count": 1,
                    },
                ],
            },
            {
                "summary": {
                    "policy_seed_now_symbols": ["XRPUSD"],
                    "policy_seed_next_symbols": ["AUDUSD"],
                }
            },
            {
                "summary": {"policy_seed_now_symbols": ["XRPUSD"]},
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
                        "suggested_seed_action": "seed_selector_profile_from_regime_truth",
                        "family_default_timeframe": "M15",
                        "family_default_base_step": 0.0004,
                        "evidence_net_usd": 2614.45,
                        "evidence_closes": 2217,
                    },
                ],
            },
            {
                "NAS100": {"buy_margin": 131.37, "sell_margin": 131.36},
                "USDCHF": {"buy_margin": 2.0, "sell_margin": 2.0},
                "USDCAD": {"buy_margin": 2.0, "sell_margin": 2.0},
                "AUDUSD": {"buy_margin": 1.44, "sell_margin": 1.44},
                "XRPUSD": {"buy_margin": 14.09, "sell_margin": 14.05},
            },
        )

        self.assertEqual(payload["summary"]["growth_ladder_symbols"], ["USDCAD", "USDCHF", "AUDUSD", "XRPUSD"])
        self.assertEqual(payload["summary"]["current_unlocked_slot_count"], 0)
        self.assertEqual(payload["summary"]["proof_lead_symbol"], "NAS100")
        self.assertEqual(payload["summary"]["proof_lead_estimated_min_lot_margin_usd"], 131.36)
        self.assertEqual(payload["summary"]["ready_for_shadow_discussion_symbols"], ["USDCAD", "USDCHF"])
        self.assertEqual(payload["summary"]["promotable_missing_launch_contract_symbols"], [])
        self.assertEqual(payload["summary"]["policy_seed_now_asset_class_counts"], {"crypto": 1})
        self.assertEqual(payload["rows"][0]["current_status"], "blocked_waiting_forward_shadow_proof")
        self.assertEqual(payload["rows"][0]["source_status"], "ready_for_shadow_discussion")
        self.assertTrue(payload["rows"][0]["machine_truth"]["starter_from_ready_for_shadow_discussion"])
        self.assertEqual(payload["rows"][1]["current_status"], "blocked_waiting_forward_shadow_proof")
        self.assertEqual(payload["rows"][2]["symbol"], "AUDUSD")
        self.assertTrue(payload["rows"][2]["machine_truth"]["fx_first_small_account_preference"])
        self.assertEqual(payload["rows"][2]["machine_truth"]["estimated_min_lot_margin_usd"], 1.44)
        self.assertEqual(payload["rows"][2]["current_status"], "blocked_missing_selector")
        self.assertEqual(payload["rows"][3]["symbol"], "XRPUSD")
        self.assertEqual(payload["summary"]["max_symbol_risk_pct_of_equity"], 0.04)
        self.assertEqual(
            payload["summary"]["starter_doctrine"],
            "starter_ready_fx_forward_proof_then_fx_first_until_heavier_margin_classes_are_affordable",
        )
        self.assertIn("full-stack follow-through first", payload["leadership_read"][1])
        self.assertIn("starter-ready follow-through", payload["leadership_read"][3])

    def test_build_payload_treats_portability_launch_contract_debt_as_not_policy_debt(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "action": "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout",
                        "machine_truth": {"lead_forward_proof_symbol": "US30"},
                    }
                ]
            },
            {
                "summary": {
                    "waiting_forward_proof_symbols": ["US30"],
                    "ready_for_shadow_discussion_symbols": ["USDCAD"],
                },
                "rows": [
                    {
                        "symbol": "US30",
                        "asset_class": "index",
                        "generalization_status": "portable_waiting_forward_proof",
                        "highest_leverage_gap": "fresh_forward_proof",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "hard_block",
                        "launch_contract_count": 2,
                    },
                    {
                        "symbol": "USDCAD",
                        "asset_class": "fx",
                        "generalization_status": "ready_for_shadow_discussion",
                        "highest_leverage_gap": "fresh_forward_proof",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "launch_contract_count": 1,
                    },
                    {
                        "symbol": "XRPUSD",
                        "asset_class": "crypto",
                        "generalization_status": "portable_missing_launch_contract",
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "launch_contract_count": 0,
                    },
                    {
                        "symbol": "AUDUSD",
                        "asset_class": "fx",
                        "generalization_status": "portable_missing_launch_contract",
                        "highest_leverage_gap": "shadow_or_live_launch_contract",
                        "guardrail_status": "promotable_now",
                        "deployment_verdict": "missing",
                        "launch_contract_count": 0,
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
                "summary": {
                    "policy_seed_now_symbols": ["XRPUSD"],
                    "policy_seed_next_symbols": ["AUDUSD"],
                },
                "rows": [
                    {
                        "symbol": "XRPUSD",
                        "asset_class": "crypto",
                        "priority": "policy_seed_now",
                        "priority_score": 70,
                        "suggested_seed_action": "reconcile_existing_policy_surfaces",
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
                ],
            },
            {
                "US30": {"buy_margin": 242.60, "sell_margin": 242.57},
                "USDCAD": {"buy_margin": 2.0, "sell_margin": 2.0},
                "AUDUSD": {"buy_margin": 1.44, "sell_margin": 1.44},
                "XRPUSD": {"buy_margin": 14.09, "sell_margin": 14.07},
            },
        )

        self.assertEqual(payload["summary"]["growth_ladder_symbols"], ["USDCAD", "XRPUSD", "AUDUSD"])
        self.assertEqual(payload["summary"]["seed_now_policy_symbols"], [])
        self.assertEqual(payload["summary"]["promotable_missing_launch_contract_symbols"], ["XRPUSD"])
        self.assertEqual(payload["rows"][1]["source_status"], "launch_contract_followthrough")
        self.assertEqual(payload["rows"][1]["current_status"], "blocked_missing_launch_contract_followthrough")
        self.assertEqual(payload["rows"][2]["symbol"], "AUDUSD")
        self.assertEqual(payload["rows"][2]["source_status"], "launch_contract_followthrough")
        self.assertEqual(payload["rows"][2]["current_status"], "blocked_missing_launch_contract_followthrough")
        self.assertIn("remaining seed-now policy coverage is `[]`", payload["leadership_read"][3])

    def test_render_markdown_mentions_unlock_ladder(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["a"],
                "summary": {
                    "current_unlocked_slot_count": 0,
                    "planned_slot_count": 1,
                    "growth_ladder_symbols": ["NAS100"],
                    "max_portfolio_risk_pct": 0.10,
                    "max_symbol_risk_pct_of_equity": 0.04,
                    "drawdown_freeze_pct": 0.05,
                    "drawdown_reduce_pct": 0.08,
                    "drawdown_block_pct": 0.10,
                },
                "rows": [
                    {
                        "slot": 1,
                        "symbol": "NAS100",
                        "asset_class": "index",
                        "source_status": "portability",
                        "current_status": "blocked_waiting_forward_proof",
                        "blocker_reason": "proof",
                        "machine_truth": {"generalization_status": "portable_waiting_forward_proof"},
                        "unlock_when": "u",
                        "kill_when": "k",
                    }
                ],
            }
        )

        self.assertIn("Hungry Hippo Account Unlock Gate Board", markdown)
        self.assertIn("Slot 1: NAS100", markdown)
        self.assertIn("blocked_waiting_forward_proof", markdown)


if __name__ == "__main__":
    unittest.main()
