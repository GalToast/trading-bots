#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_offensive_extreme_closure_shadow_board as board


class BuildOffensiveExtremeClosureShadowBoardTests(unittest.TestCase):
    def test_build_payload_prioritizes_eth_step14_after_control_restore(self) -> None:
        profit_board = {
            "rows": [
                {"theory": "offensive_extreme_closure", "machine_truth": {"policy_status": "research_candidate"}},
            ]
        }
        next_action_board = {
            "rows": [
                {"action": "prepare_eth_m5_offensive_closure_ab_only_after_control_normalization", "machine_truth": {"comparison_status": "blocked_until_control_normalized", "recommended_control_step": 14.0}},
                {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"nas100_demoted_by_fresh_window": True}},
            ]
        }
        gate_matrix = {
            "rows": [
                {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control"},
                {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample"},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "blocking_issue": "needs forward proof"},
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live", "current_truth": {"harvest_close_ticket_usd": 153.71, "escape_tier0_offensive_usd": -2074.07, "forced_unwind_usd": -572.37}},
            ]
        }
        rubric_board = {
            "rows": [
                {"candidate": "ETHUSD M5 step14 normalized control", "current_metrics": {"control_verdict": "blocked_by_stale_runtime", "control_realized_closes": 11}, "candidate_rubric": {"required_forward_closes": 25}},
                {"candidate": "BTCUSD M5 step200 salvage probe", "current_metrics": {"launch_verdict": "fail", "shadow_realized_closes": 2, "shadow_avg_per_close": 69.98}, "candidate_rubric": {"required_forward_closes": 20}},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "current_stage": "shadow_config_reconciled_waiting_forward_proof", "current_metrics": {"validation_status": "awaiting forward proof"}, "candidate_rubric": {"required_initial_shadow_positive_closes": 10, "required_validated_shadow_closes": 20}},
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "candidate_rubric": {"required_bucket_split": "harvest_vs_offensive_vs_forced_unwind"}},
            ]
        }

        payload = board.build_payload(
            profit_board,
            next_action_board,
            gate_matrix,
            rubric_board,
            {
                "summary": {"verdict": "blocked_by_stale_runtime", "target_closes": 25},
                "proof_progress": {"realized_closes": 11, "realized_net_usd": 19.53, "avg_per_close": 1.7755, "closes_remaining": 14},
            },
        )
        self.assertEqual(payload["rows"][0]["pilot"], "ETHUSD M5 step14 normalized control")
        self.assertEqual(payload["rows"][0]["status"], "first_honest_pilot_after_control_restore")
        self.assertEqual(payload["rows"][1]["status"], "second_pilot_after_contract_clean_sample_growth")
        self.assertEqual(payload["rows"][-1]["status"], "later_after_bucket_repair_and_contradiction_cleanup")
        self.assertEqual(payload["experiment_protocol"]["comparison_mode"], "shadow_only_variant_vs_baseline_same_symbol")
        self.assertIn("carry drag falls without flipping avg_per_close negative", payload["experiment_protocol"]["primary_success"])

    def test_render_markdown_mentions_first_pilot(self) -> None:
        payload = {
            "generated_at": "2026-04-15T01:00:00+00:00",
            "leadership_read": ["one"],
            "policy_status": "research_candidate",
            "experiment_protocol": {
                "comparison_mode": "shadow_only_variant_vs_baseline_same_symbol",
                "primary_success": ["reduce carry drag"],
                "failure_triggers": ["turns avg_per_close negative"],
                "anti_goals": ["not a generic stop loss"],
            },
            "summary": {"pilot_count": 1, "status_counts": {"first_shadow_pilot": 1}, "first_pilot": "ETHUSD M5"},
            "rows": [
                {
                    "priority": 1,
                    "pilot": "ETHUSD M5",
                    "status": "first_shadow_pilot",
                    "why": "best candidate",
                    "machine_truth": {"per_close": 9.01},
                    "proposed_shadow_spec": {"close_scope": "outermost_positions_only"},
                    "graduation_gate": "positive forward proof",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Offensive Extreme Closure Shadow Board", markdown)
        self.assertIn("ETHUSD M5", markdown)
        self.assertIn("first_shadow_pilot", markdown)
        self.assertIn("Experiment Protocol", markdown)
        self.assertIn("not a generic stop loss", markdown)

    def test_build_payload_uses_current_eth_normalization_truth_for_first_pilot(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "offensive_extreme_closure", "machine_truth": {"policy_status": "research_candidate"}}]},
            {
                "rows": [
                    {"action": "prepare_eth_m5_offensive_closure_ab_only_after_control_normalization", "machine_truth": {"comparison_status": "blocked_until_control_normalized", "recommended_control_step": 14.0}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"nas100_demoted_by_fresh_window": True}},
                ]
            },
            {
                "rows": [
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample"},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "blocking_issue": "needs forward proof"},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live", "current_truth": {"harvest_close_ticket_usd": 153.71, "escape_tier0_offensive_usd": -2074.07, "forced_unwind_usd": -572.37}},
                ]
            },
            {
                "rows": [
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_metrics": {"control_verdict": "blocked_by_control_normalization", "control_realized_closes": 1}, "candidate_rubric": {"required_forward_closes": 25}},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_metrics": {"launch_verdict": "fail", "shadow_realized_closes": 2, "shadow_avg_per_close": 69.98}, "candidate_rubric": {"required_forward_closes": 20}},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "current_stage": "shadow_config_reconciled_waiting_forward_proof", "current_metrics": {"validation_status": "awaiting forward proof"}, "candidate_rubric": {"required_initial_shadow_positive_closes": 10, "required_validated_shadow_closes": 20}},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "candidate_rubric": {"required_bucket_split": "harvest_vs_offensive_vs_forced_unwind"}},
                ]
            },
            {
                "summary": {"verdict": "blocked_by_control_normalization", "target_closes": 25},
                "proof_progress": {"realized_closes": 1, "realized_net_usd": -15.75, "avg_per_close": -15.75, "closes_remaining": 24},
            },
        )

        eth_row = payload["rows"][0]
        self.assertEqual(eth_row["machine_truth"]["control_verdict"], "blocked_by_control_normalization")
        self.assertIn("aligned runtime behaves like a real fixed-step control", eth_row["why"])
        self.assertIn("registered control keeps a fresh heartbeat", eth_row["proposed_shadow_spec"]["safety"])

    def test_build_payload_uses_current_eth_gate_counts_when_control_is_negative(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "offensive_extreme_closure", "machine_truth": {"policy_status": "research_candidate"}}]},
            {
                "rows": [
                    {"action": "prepare_eth_m5_offensive_closure_ab_only_after_control_normalization", "machine_truth": {"comparison_status": "ready_for_clean_control_vs_variant", "recommended_control_step": 14.0}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"nas100_demoted_by_fresh_window": True}},
                ]
            },
            {
                "rows": [
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample"},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "blocking_issue": "needs forward proof"},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live", "current_truth": {"harvest_close_ticket_usd": 153.71, "escape_tier0_offensive_usd": -2074.07, "forced_unwind_usd": -572.37}},
                ]
            },
            {
                "rows": [
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_metrics": {"control_verdict": "blocked_by_negative_expectancy", "control_realized_closes": 0}, "candidate_rubric": {"required_forward_closes": 25}},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_metrics": {"launch_verdict": "fail", "shadow_realized_closes": 2, "shadow_avg_per_close": 69.98}, "candidate_rubric": {"required_forward_closes": 20}},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "current_stage": "shadow_config_reconciled_waiting_forward_proof", "current_metrics": {"validation_status": "awaiting forward proof"}, "candidate_rubric": {"required_initial_shadow_positive_closes": 10, "required_validated_shadow_closes": 20}},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "candidate_rubric": {"required_bucket_split": "harvest_vs_offensive_vs_forced_unwind"}},
                ]
            },
            {
                "summary": {"verdict": "blocked_by_negative_expectancy", "target_closes": 25},
                "proof_progress": {"realized_closes": 12, "realized_net_usd": -176.28, "avg_per_close": -14.69, "closes_remaining": 13},
            },
        )

        eth_row = payload["rows"][0]
        self.assertEqual(eth_row["status"], "first_honest_pilot_after_positive_control_proof")
        self.assertEqual(eth_row["machine_truth"]["control_realized_closes"], 12)
        self.assertEqual(eth_row["machine_truth"]["closes_remaining"], 13)
        self.assertAlmostEqual(eth_row["machine_truth"]["control_realized_net_usd"], -176.28, places=2)
        self.assertIn("remaining blocker is positive control proof", eth_row["why"])

    def test_build_payload_keeps_btc_sell_tight_conservative_when_all_closes_are_escape_only(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "offensive_extreme_closure", "machine_truth": {"policy_status": "research_candidate"}}]},
            {
                "rows": [
                    {"action": "prepare_eth_m5_offensive_closure_ab_only_after_control_normalization", "machine_truth": {"comparison_status": "ready_for_clean_control_vs_variant", "recommended_control_step": 14.0}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"nas100_demoted_by_fresh_window": True}},
                ]
            },
            {
                "rows": [
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample"},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "blocking_issue": "forward_sample_all_escape_zero_harvest_so_far"},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live", "current_truth": {"harvest_close_ticket_usd": 153.71, "escape_tier0_offensive_usd": -2074.07, "forced_unwind_usd": -572.37}},
                ]
            },
            {
                "rows": [
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_metrics": {"control_verdict": "blocked_by_negative_expectancy", "control_realized_closes": 0}, "candidate_rubric": {"required_forward_closes": 25}},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_metrics": {"launch_verdict": "fail", "shadow_realized_closes": 2, "shadow_avg_per_close": 69.98}, "candidate_rubric": {"required_forward_closes": 20}},
                    {
                        "candidate": "BTCUSD M15 sell-tight downtrend shape",
                        "current_stage": "shadow_forward_sample_running",
                        "current_metrics": {
                            "validation_status": "retuned_2026_04_15",
                            "realized_closes": 9,
                            "realized_net_usd": -163.73,
                            "anchor_resets": 14,
                            "resets_per_close": 1.5556,
                            "reset_rate_per_hour": 14.1143,
                        },
                        "candidate_rubric": {"required_initial_shadow_positive_closes": 10, "required_validated_shadow_closes": 20},
                    },
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "candidate_rubric": {"required_bucket_split": "harvest_vs_offensive_vs_forced_unwind"}},
                ]
            },
            {
                "summary": {"verdict": "blocked_by_negative_expectancy", "target_closes": 25},
                "proof_progress": {"realized_closes": 12, "realized_net_usd": -176.28, "avg_per_close": -14.69, "closes_remaining": 13},
            },
        )

        btc_row = payload["rows"][2]
        self.assertEqual(btc_row["status"], "later_after_btc_harvest_appears")
        self.assertEqual(btc_row["machine_truth"]["close_mix_status"], "zero_harvest_all_escape_so_far")
        self.assertIn("every realized close is still escape-only", btc_row["why"])
        self.assertIn("print close_ticket harvests", btc_row["graduation_gate"])


if __name__ == "__main__":
    unittest.main()
