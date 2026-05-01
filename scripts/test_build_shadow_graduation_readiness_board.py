#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_shadow_graduation_readiness_board as board


class BuildShadowGraduationReadinessBoardTests(unittest.TestCase):
    def test_build_payload_maps_candidates_to_current_readiness(self) -> None:
        profit_board = {
            "rows": [
                {"theory": "eth_m5_no_session_gate_harvest_rebuild", "machine_truth": {"control_realized_closes": 11}},
                {"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {"current_action_bias": "SELL"}},
                {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {"shadow_realized_closes": 2, "shadow_avg_per_close": 69.98}},
                {"theory": "fx_alpha_half_universal_prior", "machine_truth": {}},
                {"theory": "index_asymmetry_family_prior", "machine_truth": {}},
            ]
        }
        eth_control_gate = {
            "summary": {"verdict": "blocked_by_stale_runtime", "realized_closes": 11, "realized_net_usd": 19.53, "avg_per_close": 1.7755},
            "control_runtime": {"runtime_stale": True, "geometry_normalized": False},
        }
        next_action_board = {
            "rows": [
                {"action": "register_eth_m5_step14_control_and_repoint_the_proof_board_to_the_same_lane", "machine_truth": {"eth_gate_verdict": "blocked_by_surface_alignment"}},
                {"action": "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate", "machine_truth": {"proof_closes": 36, "guardrail_status": "promotable_now", "deployment_gate_verdict": "manual_review"}},
                {"action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "machine_truth": {"gbpusd_proof_closes": 0, "gbpusd_guardrail_status": "contradiction", "gbp_closure_pair_live": True, "gbp_no_escape_present": True}},
            ]
        }
        launch_safety = {
            "rows": [
                {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": False},
                {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                {"config_path": "configs\\hungry_hippo_us30_m15_breakdown_sell_shadow.json", "name": "shadow_us30_m15_hungry_hippo_breakdown_sell_v1", "verdict": "fail", "gate_verdict": "hard_block", "hard_fail_reasons": ["x", "y"]},
            ]
        }
        bucket_split_summary = {
            "close_ticket": 153.71,
            "escape_tier0_offensive": -2074.07,
            "forced_unwind": -572.37,
        }
        btc_downtrend_config = {
            "enabled": False,
            "hungry_hippo_metadata": {"validation_status": "shadow_config_reconciled_2026_04_15"},
        }
        btc_reconciliation_report = {"status": "reconciled_and_ready_to_launch", "success_criteria": ["10+ closes"]}

        payload = board.build_payload(
            profit_board,
            eth_control_gate,
            next_action_board,
            launch_safety,
            bucket_split_summary,
            btc_downtrend_config,
            btc_reconciliation_report,
        )

        self.assertEqual(payload["rows"][0]["readiness"], "control_restore_required")
        self.assertEqual(payload["rows"][1]["readiness"], "shadow_reconciled_waiting_forward_proof")
        self.assertEqual(payload["rows"][2]["readiness"], "closure_policy_diagnosis_before_live")
        self.assertEqual(payload["rows"][0]["blocker"], "blocked_by_stale_runtime")
        self.assertIn("fresh heartbeat", payload["rows"][0]["next_move"])
        self.assertEqual(payload["rows"][2]["blocker"], "paired_forward_sample_not_ready_yet")
        self.assertIn("paired forward closes", payload["rows"][2]["next_move"])
        self.assertTrue(payload["rows"][2]["evidence"]["closure_pair_live"])
        self.assertEqual(payload["rows"][3]["readiness"], "research_only_shadow_candidate")
        self.assertEqual(payload["rows"][-1]["readiness"], "blocked_before_live_discussion")

    def test_render_markdown_mentions_candidate(self) -> None:
        payload = {
            "generated_at": "2026-04-15T01:00:00+00:00",
            "leadership_read": ["one"],
            "summary": {"candidate_count": 1, "readiness_counts": {"control_restore_required": 1}, "top_candidates": ["ETHUSD M5 step14 normalized control"]},
            "rows": [
                {
                    "priority": 1,
                    "candidate": "ETHUSD M5 step14 normalized control",
                    "readiness": "control_restore_required",
                    "source_theory": "eth",
                    "evidence": {"avg_per_close": 1.7755},
                    "blocker": "blocked_by_stale_runtime",
                    "next_move": "restore control",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Shadow Graduation Readiness Board", markdown)
        self.assertIn("ETHUSD M5 step14 normalized control", markdown)
        self.assertIn("control_restore_required", markdown)

    def test_parse_helpers_extract_current_repo_signals(self) -> None:
        bucket_text = (
            "The GBPUSD HH bucket breakdown reveals that **core harvest (close_ticket) is profitable** "
            "(+$153.71) but **escape_tier0_offensive (-$2,074.07) and forced_unwind (-$572.37) destroy all profits and more**."
        )
        report_text = "**Status:** `reconciled_and_ready_to_launch`\n\n**Success criteria for forward proof:**\n- 10+ closes\n- avg_per_close positive\n---\n"

        bucket = board.parse_bucket_split_summary(bucket_text)
        report = board.parse_btc_reconciliation_markdown(report_text)

        self.assertEqual(bucket["close_ticket"], 153.71)
        self.assertEqual(bucket["forced_unwind"], -572.37)
        self.assertEqual(report["status"], "reconciled_and_ready_to_launch")
        self.assertEqual(report["success_criteria"][0], "10+ closes")

    def test_build_payload_uses_live_btc_v2_sample_when_state_exists(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {"theory": "eth_m5_no_session_gate_harvest_rebuild", "machine_truth": {}},
                    {"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {}},
                    {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {}},
                    {"theory": "fx_alpha_half_universal_prior", "machine_truth": {}},
                    {"theory": "index_asymmetry_family_prior", "machine_truth": {}},
                ]
            },
            {"summary": {"verdict": "blocked_by_negative_expectancy"}, "control_runtime": {"runtime_stale": False, "geometry_normalized": True}},
            {
                "rows": [
                    {"action": "keep_eth_m5_step14_control_running_as_the_single_proof_lane", "machine_truth": {"eth_gate_verdict": "blocked_by_negative_expectancy"}},
                    {"action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "machine_truth": {"gbpusd_proof_closes": 0, "gbpusd_guardrail_status": "contradiction", "gbp_closure_pair_live": True, "gbp_no_escape_present": True}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"proof_closes": 0, "guardrail_status": "promotable_now", "deployment_gate_verdict": "hard_block"}},
                ]
            },
            {
                "rows": [
                    {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": True},
                    {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                    {"config_path": "configs\\hungry_hippo_us30_m15_breakdown_sell_shadow.json", "name": "shadow_us30_m15_hungry_hippo_breakdown_sell_v1", "verdict": "fail", "gate_verdict": "hard_block", "hard_fail_reasons": ["x"]},
                ]
            },
            {"close_ticket": 153.71, "escape_tier0_offensive": -2074.07, "forced_unwind": -572.37},
            {
                "enabled": True,
                "stale_after_seconds": 0,
                "hungry_hippo_metadata": {
                    "validation_status": "v2_retuned_2026_04_15",
                    "guardrails": {"max_resets_per_close": 2.0, "max_resets_per_hour": 6},
                },
            },
            {
                "runner": {"started_at": "2026-04-15T17:59:57+00:00", "heartbeat_at": "2026-04-15T18:10:29+00:00"},
                "symbols": {"BTCUSD": {"realized_closes": 9, "realized_net_usd": -163.73, "anchor_resets": 11}},
                "updated_at": "2026-04-15T18:10:29+00:00",
            },
            {"status": "reconciled_and_ready_to_launch", "success_criteria": ["10+ closes"]},
            {"v2_close_mix": {"total_close_events": 9, "harvest_closes": 0, "escape_tier2_surgical_closes": 9, "harvest_share": 0.0, "close_mix_status": "zero_harvest_all_escape_so_far", "all_closes_escape_dominated": True}},
        )

        btc_row = payload["rows"][1]
        self.assertEqual(btc_row["readiness"], "shadow_forward_sample_running")
        self.assertEqual(btc_row["blocker"], "forward_sample_all_escape_zero_harvest_so_far")
        self.assertEqual(btc_row["evidence"]["realized_closes"], 9)
        self.assertAlmostEqual(btc_row["evidence"]["resets_per_close"], 11 / 9, places=4)
        self.assertEqual(btc_row["evidence"]["btc_harvest_closes"], 0)
        self.assertEqual(btc_row["evidence"]["btc_escape_tier2_surgical_closes"], 9)
        self.assertEqual(btc_row["evidence"]["btc_close_mix_status"], "zero_harvest_all_escape_so_far")
        self.assertIn("first close_ticket harvest appears", btc_row["next_move"])

    def test_eth_negative_expectancy_maps_to_positive_proof_readiness(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {"theory": "eth_m5_no_session_gate_harvest_rebuild", "machine_truth": {}},
                    {"theory": "btc_m15_downtrend_sell_tight_shape", "machine_truth": {}},
                    {"theory": "btc_m5_step200_salvage_probe", "machine_truth": {}},
                    {"theory": "fx_alpha_half_universal_prior", "machine_truth": {}},
                    {"theory": "index_asymmetry_family_prior", "machine_truth": {}},
                ]
            },
            {"summary": {"verdict": "blocked_by_negative_expectancy", "realized_closes": 12, "realized_net_usd": -176.28, "avg_per_close": -14.69}, "control_runtime": {"runtime_stale": False, "geometry_normalized": True}},
            {
                "rows": [
                    {"action": "keep_eth_m5_step14_control_running_as_the_single_proof_lane", "machine_truth": {"eth_gate_verdict": "blocked_by_negative_expectancy"}},
                    {"action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "machine_truth": {"gbpusd_proof_closes": 0, "gbpusd_guardrail_status": "contradiction", "gbp_closure_pair_live": True, "gbp_no_escape_present": True}},
                    {"action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves", "machine_truth": {"proof_closes": 0, "guardrail_status": "promotable_now", "deployment_gate_verdict": "hard_block"}},
                ]
            },
            {
                "rows": [
                    {"config_path": "configs\\hungry_hippo_btcusd_m15_sell_tight_shadow.json", "name": "shadow_btcusd_m15_sell_tight_v1", "verdict": "research_only", "enabled": True},
                    {"config_path": "configs\\hungry_hippo_btcusd_m5_step200_shadow.json", "name": "shadow_btcusd_m5_hungry_hippo_step200_v1", "verdict": "fail", "enabled": False},
                    {"config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json", "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1", "verdict": "research_only", "enabled": True},
                    {"config_path": "configs\\hungry_hippo_us30_m15_breakdown_sell_shadow.json", "name": "shadow_us30_m15_hungry_hippo_breakdown_sell_v1", "verdict": "fail", "gate_verdict": "hard_block", "hard_fail_reasons": ["x"]},
                ]
            },
            {"close_ticket": 153.71, "escape_tier0_offensive": -2074.07, "forced_unwind": -572.37},
            {"enabled": True, "hungry_hippo_metadata": {"validation_status": "v2_retuned_2026_04_15"}},
            {"status": "reconciled_and_ready_to_launch", "success_criteria": ["10+ closes"]},
            {"runner": {}, "symbols": {"BTCUSD": {"realized_closes": 0, "realized_net_usd": 0.0, "anchor_resets": 0}}, "updated_at": ""},
            {"v2_close_mix": {"total_close_events": 0, "harvest_closes": 0, "escape_tier2_surgical_closes": 0, "harvest_share": 0.0, "close_mix_status": "no_closes_yet", "all_closes_escape_dominated": False}},
        )

        self.assertEqual(payload["rows"][0]["readiness"], "control_positive_proof_required")
        self.assertIn("positive forward proof", payload["rows"][0]["next_move"])


if __name__ == "__main__":
    unittest.main()
