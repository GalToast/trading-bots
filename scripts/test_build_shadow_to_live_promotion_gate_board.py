#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_shadow_to_live_promotion_gate_board as board


class BuildShadowToLivePromotionGateBoardTests(unittest.TestCase):
    def test_build_payload_orders_current_candidates(self) -> None:
        profit_board = {
            "rows": [
                {"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {}},
                {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {"shadow_avg_per_close": 69.98, "shadow_realized_closes": 2, "hold_gate": "hold_until_buy_realign", "live_m15_baseline_avg_per_close": 4.58}},
            ]
        }
        readiness_board = {
            "rows": [
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "readiness": "shadow_config_exists_needs_reconcile", "evidence": {"current_action_bias": "SELL", "current_control_mode": "bounce_reversal", "proposed_sell_step": 129.7, "proposed_alpha": 0.3}, "blocker": "needs reconcile"},
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "live_candidate_guardrail_aligned", "evidence": {"per_close": 1.84, "closes": 111, "guardrail_status": "aligned"}, "blocker": "contradiction"},
                {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {}, "blocker": "small sample"},
                {"candidate": "NAS100 asym breakout family lane", "readiness": "forward_positive_waiting_window", "evidence": {"per_close": 2.89, "closes": 36, "guardrail_status": "aligned", "next_action": "wait_for_session_window"}, "blocker": "window"},
                {"candidate": "US30 asym breakout family lane", "readiness": "positive_shadow_guardrail_blocked", "evidence": {"per_close": 27.28, "closes": 7, "guardrail_status": "blocked", "next_action": "unblock_guardrails_first"}, "blocker": "blocked"},
            ]
        }
        controller_priors = {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}}
        eth_control_gate = {
            "summary": {"verdict": "blocked_by_stale_runtime", "realized_closes": 11, "avg_per_close": 1.77, "comparison_status": "blocked_until_control_normalized"},
            "control_runtime": {"runtime_stale": True, "geometry_normalized": False},
            "advance_when": ["fresh heartbeat", "normalized ladder"],
        }
        next_action_board = {
            "rows": [
                {"action": "register_eth_m5_step14_control_and_repoint_the_proof_board_to_the_same_lane", "machine_truth": {"eth_gate_verdict": "blocked_by_surface_alignment"}},
                {"action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "machine_truth": {"gbpusd_proof_closes": 0, "gbpusd_guardrail_status": "contradiction", "gbp_closure_pair_live": True, "gbp_no_escape_present": True}},
                {"action": "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate", "machine_truth": {"proof_closes": 36, "guardrail_status": "promotable_now", "deployment_gate_verdict": "manual_review"}},
            ]
        }
        launch_safety = {
            "rows": [
                {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": False},
                {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
            ]
        }
        bucket_split_summary = {
            "close_ticket": 153.71,
            "escape_tier0_offensive": -2074.07,
            "forced_unwind": -572.37,
        }
        btc_downtrend_config = {
            "enabled": False,
            "hungry_hippo_metadata": {
                "validation_status": "shadow_config_reconciled_2026_04_15",
            },
        }
        btc_reconciliation_report = {
            "status": "reconciled_and_ready_to_launch",
            "success_criteria": [
                "10+ closes under SELL bias conditions",
                "avg_per_close positive",
            ],
        }

        payload = board.build_payload(
            profit_board,
            readiness_board,
            controller_priors,
            eth_control_gate,
            next_action_board,
            launch_safety,
            bucket_split_summary,
            btc_downtrend_config,
            btc_reconciliation_report,
        )

        self.assertEqual(payload["rows"][0]["candidate"], "ETHUSD M5 step14 normalized control")
        self.assertEqual(payload["rows"][0]["promotion_verdict"], "restore_control_then_validate_shadow")
        self.assertEqual(payload["rows"][0]["blocking_issue"], "blocked_by_stale_runtime")
        self.assertIn("canonical lane", payload["rows"][0]["live_read"])
        self.assertEqual(payload["rows"][1]["promotion_verdict"], "bucket_diagnosis_before_live")
        self.assertEqual(payload["rows"][1]["blocking_issue"], "paired forward sample not mature enough yet")
        self.assertIn("keep both GBP lanes alive", payload["rows"][1]["promotion_gate"][0])
        self.assertIn("baseline-vs-no-escape forward sample", payload["rows"][1]["live_read"])
        self.assertEqual(payload["rows"][2]["promotion_verdict"], "cleanest_shadow_candidate_after_control_work")
        self.assertEqual(payload["rows"][3]["promotion_verdict"], "collect_forward_proof_then_judge")
        self.assertEqual(payload["rows"][3]["machine_truth"]["reconciliation_status"], "reconciled_and_ready_to_launch")
        self.assertIn("10+ closes under SELL bias conditions", payload["rows"][3]["promotion_gate"])
        self.assertEqual(payload["rows"][-1]["promotion_verdict"], "blocked_before_live_discussion")
        self.assertEqual(payload["summary"]["closest_current_live_candidate"], "none_honest_yet")

    def test_build_payload_uses_direct_eth_gate_verdict_for_normalization_state(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {}}, {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {}}]},
            {
                "rows": [
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "readiness": "shadow_reconciled_waiting_forward_proof", "evidence": {}, "blocker": "needs forward proof"},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {}, "blocker": "contradiction"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {}, "blocker": "small sample"},
                    {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {}, "blocker": "window"},
                    {"candidate": "US30 asym breakout family lane", "readiness": "blocked_before_live_discussion", "evidence": {}, "blocker": "blocked"},
                ]
            },
            {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}},
            {
                "summary": {"verdict": "blocked_by_control_normalization", "realized_closes": 1, "avg_per_close": -15.75, "comparison_status": "blocked_until_control_normalized"},
                "control_runtime": {"runtime_stale": False, "geometry_normalized": False},
                "advance_when": ["fresh heartbeat", "normalized ladder"],
            },
            {
                "rows": [
                    {"action": "register_eth_m5_step14_control_and_repoint_the_proof_board_to_the_same_lane", "machine_truth": {"eth_gate_verdict": "blocked_by_surface_alignment"}},
                    {"action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "machine_truth": {"gbpusd_proof_closes": 0, "gbpusd_guardrail_status": "contradiction", "gbp_closure_pair_live": True, "gbp_no_escape_present": True}},
                    {"action": "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate", "machine_truth": {"proof_closes": 36, "guardrail_status": "promotable_now", "deployment_gate_verdict": "manual_review"}},
                ]
            },
            {
                "rows": [
                    {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                ]
            },
            {"close_ticket": 153.71, "escape_tier0_offensive": -2074.07, "forced_unwind": -572.37},
            {"enabled": False, "hungry_hippo_metadata": {"validation_status": "shadow_config_reconciled_2026_04_15"}},
            {"status": "reconciled_and_ready_to_launch", "success_criteria": ["10+ closes"]},
        )

        self.assertEqual(payload["rows"][0]["blocking_issue"], "blocked_by_control_normalization")
        self.assertIn("aligned and the heartbeat is fresh", payload["rows"][0]["live_read"])

    def test_build_payload_uses_positive_proof_stage_when_eth_is_aligned_but_negative(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {}}, {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {}}]},
            {
                "rows": [
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "readiness": "shadow_reconciled_waiting_forward_proof", "evidence": {}, "blocker": "needs forward proof"},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {}, "blocker": "contradiction"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {}, "blocker": "small sample"},
                    {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {}, "blocker": "window"},
                    {"candidate": "US30 asym breakout family lane", "readiness": "blocked_before_live_discussion", "evidence": {}, "blocker": "blocked"},
                ]
            },
            {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}},
            {
                "summary": {"verdict": "blocked_by_negative_expectancy", "realized_closes": 12, "avg_per_close": -14.69, "comparison_status": "ready_for_clean_control_vs_variant"},
                "control_runtime": {"runtime_stale": False, "geometry_normalized": True},
                "advance_when": ["25 positive closes"],
            },
            {
                "rows": [
                    {"action": "keep_eth_m5_step14_control_running_as_the_single_proof_lane", "machine_truth": {"eth_gate_verdict": "blocked_by_negative_expectancy"}},
                    {"action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "machine_truth": {"gbpusd_proof_closes": 0, "gbpusd_guardrail_status": "contradiction", "gbp_closure_pair_live": True, "gbp_no_escape_present": True}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"proof_closes": 36, "guardrail_status": "promotable_now", "deployment_gate_verdict": "manual_review"}},
                ]
            },
            {
                "rows": [
                    {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                ]
            },
            {"close_ticket": 153.71, "escape_tier0_offensive": -2074.07, "forced_unwind": -572.37},
            {"enabled": False, "hungry_hippo_metadata": {"validation_status": "shadow_config_reconciled_2026_04_15"}},
            {"status": "reconciled_and_ready_to_launch", "success_criteria": ["10+ closes"]},
        )

        self.assertEqual(payload["rows"][0]["current_stage"], "tested_theory_waiting_for_positive_control_proof")
        self.assertEqual(payload["rows"][0]["promotion_verdict"], "collect_positive_control_proof_before_validated_shadow")
        self.assertIn("comparison hygiene is ready", payload["rows"][0]["live_read"])

    def test_render_markdown_mentions_live_candidate(self) -> None:
        payload = {
            "generated_at": "2026-04-15T01:00:00+00:00",
            "leadership_read": ["one"],
            "summary": {"candidate_count": 1, "promotion_verdict_counts": {"restore_control_then_validate_shadow": 1}, "closest_current_live_candidate": "none_honest_yet"},
            "rows": [
                {
                    "priority": 1,
                    "candidate": "ETHUSD M5 step14 normalized control",
                    "current_stage": "tested_theory_waiting_for_clean_control",
                    "promotion_verdict": "restore_control_then_validate_shadow",
                    "machine_truth": {"avg_per_close": 1.84},
                    "blocking_issue": "blocked_by_stale_runtime",
                    "promotion_gate": ["restore control"],
                    "live_read": "not a live candidate",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Shadow To Live Promotion Gate Board", markdown)
        self.assertIn("ETHUSD M5 step14 normalized control", markdown)
        self.assertIn("none_honest_yet", markdown)

    def test_parse_bucket_split_summary_reads_current_numbers(self) -> None:
        text = (
            "The GBPUSD HH bucket breakdown reveals that **core harvest (close_ticket) is profitable** "
            "(+$153.71) but **escape_tier0_offensive (-$2,074.07) and forced_unwind (-$572.37) destroy all profits and more**."
        )

        parsed = board.parse_bucket_split_summary(text)

        self.assertEqual(parsed["close_ticket"], 153.71)
        self.assertEqual(parsed["escape_tier0_offensive"], -2074.07)
        self.assertEqual(parsed["forced_unwind"], -572.37)

    def test_parse_btc_reconciliation_markdown_reads_status_and_criteria(self) -> None:
        text = (
            "**Status:** `reconciled_and_ready_to_launch`\n\n"
            "**Success criteria for forward proof:**\n"
            "- 10+ closes under SELL bias conditions\n"
            "- avg_per_close positive\n"
            "---\n"
        )

        parsed = board.parse_btc_reconciliation_markdown(text)

        self.assertEqual(parsed["status"], "reconciled_and_ready_to_launch")
        self.assertEqual(parsed["success_criteria"][0], "10+ closes under SELL bias conditions")

    def test_btc_machine_truth_falls_back_to_config_metadata_when_readiness_evidence_is_sparse(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "btc_m5_step200_salvage_probe", "machine_truth": {}}]},
            {
                "rows": [
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "readiness": "shadow_reconciled_waiting_forward_proof", "evidence": {}},
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {}, "blocker": "x"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {}, "blocker": "x"},
                    {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {}, "blocker": "x"},
                    {"candidate": "US30 asym breakout family lane", "readiness": "blocked_before_live_discussion", "evidence": {}, "blocker": "x"},
                ]
            },
            {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}},
            {"summary": {}, "control_runtime": {}, "advance_when": []},
            {
                "rows": [
                    {"action": "verify_or_restore_eth_m5_step14_control_runtime_before_treating_it_as_the_proof_lane", "machine_truth": {}},
                    {"action": "treat_gbpusd_alpha_half_as_bucket_diagnosis_before_any_promotion_or_default_story", "machine_truth": {}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {}},
                ]
            },
            {
                "rows": [
                    {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                ]
            },
            {"close_ticket": 0.0, "escape_tier0_offensive": 0.0, "forced_unwind": 0.0},
            {
                "enabled": False,
                "hungry_hippo_metadata": {
                    "action_bias": "SELL",
                    "control_mode": "bounce_reversal",
                    "computed_sell_step": 129.71464,
                    "validation_status": "shadow_config_reconciled_2026_04_15",
                },
            },
            {"status": "reconciled_and_ready_to_launch", "success_criteria": ["10+ closes"]},
        )

        btc_row = payload["rows"][3]
        self.assertEqual(btc_row["machine_truth"]["action_bias"], "SELL")
        self.assertEqual(btc_row["machine_truth"]["control_mode"], "bounce_reversal")
        self.assertEqual(btc_row["machine_truth"]["proposed_sell_step"], 129.71464)

    def test_btc_promotion_gate_prefers_live_v2_guardrails_over_stale_report_thresholds(self) -> None:
        payload = board.build_payload(
            {"rows": [{"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {}}, {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {}}]},
            {
                "rows": [
                    {
                        "candidate": "BTCUSD M15 sell-tight downtrend shape",
                        "readiness": "shadow_forward_sample_running",
                        "evidence": {
                            "current_action_bias": "SELL",
                            "current_control_mode": "bounce_reversal",
                            "proposed_sell_step": 259.43,
                            "max_resets_per_hour": 6.0,
                            "max_resets_per_close": 2.0,
                            "realized_closes": 9,
                            "realized_net_usd": -163.73,
                            "btc_total_close_events": 9,
                            "btc_harvest_closes": 0,
                            "btc_escape_tier2_surgical_closes": 9,
                            "btc_harvest_share": 0.0,
                            "btc_close_mix_status": "zero_harvest_all_escape_so_far",
                            "btc_all_closes_escape_dominated": True,
                        },
                        "blocker": "forward_sample_all_escape_zero_harvest_so_far",
                    },
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {}, "blocker": "x"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {}, "blocker": "x"},
                    {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {}, "blocker": "x"},
                    {"candidate": "US30 asym breakout family lane", "readiness": "blocked_before_live_discussion", "evidence": {}, "blocker": "x"},
                ]
            },
            {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}},
            {"summary": {}, "control_runtime": {}, "advance_when": []},
            {
                "rows": [
                    {"action": "verify_or_restore_eth_m5_step14_control_runtime_before_treating_it_as_the_proof_lane", "machine_truth": {}},
                    {"action": "treat_gbpusd_alpha_half_as_bucket_diagnosis_before_any_promotion_or_default_story", "machine_truth": {}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {}},
                ]
            },
            {
                "rows": [
                    {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": True},
                    {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                ]
            },
            {"close_ticket": 0.0, "escape_tier0_offensive": 0.0, "forced_unwind": 0.0},
            {
                "enabled": True,
                "max_floating_loss_usd": -15.0,
                "hungry_hippo_metadata": {
                    "action_bias": "SELL",
                    "control_mode": "bounce_reversal",
                    "computed_sell_step": 259.43,
                    "validation_status": "v2_retuned_2026_04_15",
                    "guardrails": {"max_resets_per_hour": 6, "max_resets_per_close": 2.0, "floating_loss_limit_usd": -15.0},
                },
            },
            {
                "status": "reconciled_and_ready_to_launch",
                "success_criteria": [
                    "10+ closes under SELL bias conditions",
                    "avg_per_close positive",
                    "Zero reset storms (reset rate < 2/hour)",
                    "Floating loss stays within -$15 guardrail",
                ],
            },
        )

        btc_gate = payload["rows"][3]["promotion_gate"]
        self.assertIn("Reset rate stays <= 6.0/hour", btc_gate)
        self.assertIn("Resets per close stay <= 2.0", btc_gate)
        self.assertTrue(any("require the close mix to stop being all-escape" in item for item in btc_gate))
        self.assertNotIn("Zero reset storms (reset rate < 2/hour)", btc_gate)
        self.assertEqual(payload["rows"][3]["machine_truth"]["btc_close_mix_status"], "zero_harvest_all_escape_so_far")
        self.assertIn("zero harvest", payload["rows"][3]["live_read"])


if __name__ == "__main__":
    unittest.main()
