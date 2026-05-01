#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_research_contribution_board as board


class BuildHungryHippoResearchContributionBoardTests(unittest.TestCase):
    def test_build_payload_prefers_launch_contract_followthrough_when_cleared_symbols_exist(self) -> None:
        controller_priors = {
            "symbol_priors": {
                "GBPUSD": {"evidence": {"gbp_rearm_avg_per_close": 3.95}},
                "EURUSD": {"evidence": {"eur_rearm_avg_per_close": 3.17}},
            },
        }
        profit_board = {
            "rows": [
                {
                    "theory": "offensive_extreme_closure",
                    "stage": "shadow_spec_ready",
                    "machine_truth": {"policy_status": "research_candidate", "graduation_gate": "requires proof"},
                },
                {
                    "theory": "dual_lattice_hedge_wave_cancellation",
                    "stage": "simulation_required",
                    "machine_truth": {"policy_status": "research_candidate"},
                },
            ]
        }
        readiness_board = {
            "summary": {"top_candidates": ["ETHUSD M5 step14 normalized control"]},
            "rows": [
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "evidence": {"closes": 111, "per_close": 1.84, "guardrail_status": "aligned"}},
                {"candidate": "ETHUSD M5 step14 normalized control", "evidence": {"closes": 20, "per_close": 7.85}},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "evidence": {}},
            ],
        }
        promotion_gate = {
            "rows": [
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "blocking_issue": "selector contradiction"},
                {"candidate": "ETHUSD M5 step14 normalized control", "blocking_issue": "needs fresh forward proof", "machine_truth": {"shadow_avg_per_close": 7.85, "shadow_realized_closes": 20, "live_reference_avg_per_close": -9.21, "promotion_action": "unblock_guardrails_first"}},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "promotion_verdict": "reconcile_shadow_then_judge", "blocking_issue": "needs reconcile", "machine_truth": {"action_bias": "SELL", "control_mode": "bounce_reversal", "proposed_sell_step": 129.7}},
                {"candidate": "NAS100 asym breakout family lane", "promotion_verdict": "window_and_regime_gated", "machine_truth": {"next_action": "wait_for_session_window"}},
                {"candidate": "US30 asym breakout family lane", "promotion_verdict": "blocked_before_live_discussion", "machine_truth": {"guardrail_status": "blocked"}},
            ]
        }
        rubric_board = {
            "rows": [
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "shadow_to_live_rubric": {"required_contradictions": "zero"}},
                {"candidate": "ETHUSD M5 step14 normalized control", "shadow_to_live_rubric": {"required_forward_closes": 25}},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "shadow_to_live_rubric": {"required_forward_closes": 20}},
            ]
        }
        guardrail_audit = {
            "summary": {"promotable_now_symbols": ["NAS100"]},
            "rows": [
                {"symbol": "GBPUSD", "status": "contradiction"},
                {"symbol": "ETHUSD", "status": "blocked_by_guardrail"},
                {"symbol": "NAS100", "status": "promotable_now"},
                {"symbol": "US30", "status": "blocked_by_guardrail"},
            ],
        }
        next_action_board = {
            "summary": {
                "top_priority_action": "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
            },
            "rows": [
                {
                    "action": "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
                    "machine_truth": {
                        "eth_gate_verdict": "blocked_by_surface_alignment",
                        "runtime_stale": True,
                        "enabled_alignment_ok": False,
                    },
                },
                {
                    "action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
                    "machine_truth": {
                        "guardrail_status": "promotable_now",
                    },
                },
            ],
        }
        portability_board = {
            "summary": {
                "family_portable_count": 19,
                "surface_coverage_complete_count": 6,
                "waiting_forward_proof_symbols": ["NAS100"],
                "missing_policy_symbols": ["AUDUSD", "USDCHF", "XAGUSD"],
                "missing_launch_contract_symbols": ["USDCHF", "USDCAD"],
            },
            "rows": [
                {
                    "symbol": "USDCHF",
                    "generalization_status": "portable_missing_launch_contract",
                    "guardrail_status": "promotable_now",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                },
                {
                    "symbol": "USDCAD",
                    "generalization_status": "portable_missing_launch_contract",
                    "guardrail_status": "promotable_now",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                },
                {
                    "symbol": "USDJPY",
                    "generalization_status": "portable_missing_launch_contract",
                    "guardrail_status": "promotable_now",
                    "deployment_verdict": "manual_review",
                },
            ],
        }
        policy_gap_board = {
            "summary": {
                "policy_seed_now_symbols": ["XRPUSD"],
                "policy_seed_next_symbols": ["AUDUSD"],
            }
        }

        payload = board.build_payload(
            controller_priors,
            profit_board,
            readiness_board,
            promotion_gate,
            rubric_board,
            guardrail_audit,
            next_action_board,
            portability_board,
            policy_gap_board,
        )

        self.assertEqual(payload["best_overall_contribution"], "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane")
        self.assertEqual(payload["best_any_symbol_contribution"], "add_checked_in_launch_contracts_for_promotable_portable_symbols")
        self.assertEqual(payload["best_nonruntime_contribution"], "offensive_extreme_closure_same_symbol_experiment")
        self.assertEqual(payload["research_areas"][0]["area"], "any_symbol_portability_followthrough_gap")
        self.assertEqual(payload["contribution_lanes"][0]["fit"], "best_any_symbol_generalization_move")
        self.assertEqual(payload["contribution_lanes"][0]["machine_truth"]["promotable_missing_launch_contract_symbols"], ["USDCHF", "USDCAD"])
        self.assertEqual(payload["contribution_lanes"][1]["fit"], "best_room_move")
        self.assertEqual(payload["contribution_lanes"][5]["fit"], "best_codex_nonruntime_move")

    def test_build_payload_accepts_legacy_eth_step5_alias_and_falls_back_to_readiness_metrics(self) -> None:
        payload = board.build_payload(
            controller_priors={
                "symbol_priors": {
                    "GBPUSD": {"evidence": {"gbp_rearm_avg_per_close": 3.95}},
                    "EURUSD": {"evidence": {"eur_rearm_avg_per_close": 3.17}},
                }
            },
            profit_board={
                "rows": [
                    {"theory": "offensive_extreme_closure", "stage": "shadow_spec_ready", "machine_truth": {"policy_status": "research_candidate", "graduation_gate": "requires proof"}},
                    {"theory": "dual_lattice_hedge_wave_cancellation", "stage": "simulation_required", "machine_truth": {"policy_status": "research_candidate"}},
                ]
            },
            readiness_board={
                "summary": {"top_candidates": ["ETHUSD M5 step5 Hungry Hippo rebuild"]},
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "evidence": {"closes": 111, "per_close": 1.84, "guardrail_status": "aligned"}},
                    {"candidate": "ETHUSD M5 step5 Hungry Hippo rebuild", "evidence": {"closes": 22, "per_close": 6.1}},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "evidence": {}},
                ],
            },
            promotion_gate={
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "blocking_issue": "selector contradiction"},
                    {"candidate": "ETHUSD M5 step5 Hungry Hippo rebuild", "blocking_issue": "needs fresh forward proof", "machine_truth": {"live_reference_avg_per_close": -9.21, "promotion_action": "unblock_guardrails_first"}},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "promotion_verdict": "reconcile_shadow_then_judge", "blocking_issue": "needs reconcile", "machine_truth": {"action_bias": "SELL", "control_mode": "bounce_reversal", "proposed_sell_step": 129.7}},
                    {"candidate": "NAS100 asym breakout family lane", "promotion_verdict": "window_and_regime_gated", "machine_truth": {"next_action": "wait_for_session_window"}},
                    {"candidate": "US30 asym breakout family lane", "promotion_verdict": "blocked_before_live_discussion", "machine_truth": {"guardrail_status": "blocked"}},
                ]
            },
            rubric_board={
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "shadow_to_live_rubric": {"required_contradictions": "zero"}},
                    {"candidate": "ETHUSD M5 step5 Hungry Hippo rebuild", "shadow_to_live_rubric": {"required_forward_closes": 25}},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "shadow_to_live_rubric": {"required_forward_closes": 20}},
                ]
            },
            guardrail_audit={
                "rows": [
                    {"symbol": "GBPUSD", "status": "contradiction"},
                    {"symbol": "ETHUSD", "status": "blocked_by_guardrail"},
                    {"symbol": "NAS100", "status": "promotable_now"},
                ],
            },
            next_action_board={
                "rows": [
                    {
                        "action": "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
                        "machine_truth": {"eth_gate_verdict": "blocked_by_surface_alignment", "runtime_stale": True, "enabled_alignment_ok": False},
                    },
                    {
                        "action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
                        "machine_truth": {},
                    },
                ]
            },
            portability_board={
                "summary": {
                    "family_portable_count": 19,
                    "surface_coverage_complete_count": 6,
                    "waiting_forward_proof_symbols": ["NAS100"],
                    "missing_policy_symbols": ["AUDUSD"],
                    "missing_launch_contract_symbols": [],
                }
            },
            policy_gap_board={
                "summary": {
                    "policy_seed_now_symbols": [],
                    "policy_seed_next_symbols": ["AUDUSD"],
                }
            },
        )

        self.assertEqual(payload["best_any_symbol_contribution"], "expand_canonical_policy_coverage_for_portable_missing_policy_symbols")
        self.assertIn("launch-contract seam is closed", payload["leadership_read"][1])
        self.assertIn("remaining canonical policy queue", payload["research_areas"][0]["contribution_implication"])
        self.assertEqual(payload["research_areas"][2]["area"], "eth_m5_control_rebuild_validation")
        self.assertEqual(payload["contribution_lanes"][4]["machine_truth"]["shadow_closes"], 22)
        self.assertEqual(payload["contribution_lanes"][4]["machine_truth"]["shadow_avg_per_close"], 6.1)

    def test_render_markdown_mentions_best_calls(self) -> None:
        payload = {
            "generated_at": "2026-04-15T01:00:00+00:00",
            "leadership_read": ["one"],
            "best_overall_contribution": "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
            "best_any_symbol_contribution": "expand_canonical_policy_coverage_for_portable_missing_policy_symbols",
            "best_nonruntime_contribution": "offensive_extreme_closure_same_symbol_experiment",
            "research_areas": [
                {
                    "priority": 1,
                    "area": "any_symbol_policy_coverage_gap",
                    "maturity": "durable_truth",
                    "why_it_matters": "important",
                    "machine_truth": {"gbpusd_avg_per_close": 3.95},
                    "contribution_implication": "reconcile",
                }
            ],
            "contribution_lanes": [
                {
                    "priority": 1,
                    "lane": "reconcile_gbpusd_alpha_half_live_path",
                    "fit": "best_room_move",
                    "why_this_is_best": "fastest",
                    "machine_truth": {"forward_closes": 111},
                    "first_artifact": "note",
                    "reason_to_pick_this_over_new_theory": "already proven",
                }
            ],
            "avoid_now": [
                {"idea": "dual_lattice_hedge_wave_cancellation", "why_not_now": "needs simulation"}
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Hungry Hippo Research Contribution Board", markdown)
        self.assertIn("Best overall contribution", markdown)
        self.assertIn("Best any-symbol contribution", markdown)
        self.assertIn("reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane", markdown)
        self.assertIn("dual_lattice_hedge_wave_cancellation", markdown)


if __name__ == "__main__":
    unittest.main()
