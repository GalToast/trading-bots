#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_next_action_board as board


class BuildHungryHippoNextActionBoardTests(unittest.TestCase):
    def test_config_row_falls_back_to_symbol_candidate_when_exact_filename_changes(self) -> None:
        payload = {
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_v2_shadow.json", "verdict": "fail"},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_v3_shadow.json", "verdict": "research_only"},
            ]
        }

        eth_row = board.config_row(
            payload,
            "hungry_hippo_ethusd_m5_step5_shadow.json",
            symbol="ETHUSD",
            preferred_terms=["step5"],
        )
        btc_row = board.config_row(
            payload,
            "hungry_hippo_btcusd_m15_sell_tight_shadow.json",
            symbol="BTCUSD",
            preferred_terms=["sell_tight"],
        )

        self.assertEqual(eth_row["config_path"], "configs/hungry_hippo_ethusd_m5_step5_v2_shadow.json")
        self.assertEqual(btc_row["config_path"], "configs/hungry_hippo_btcusd_m15_sell_tight_v3_shadow.json")

    def test_refresh_inputs_rebuilds_eth_proof_gate_board(self) -> None:
        with patch.object(board, "run_builder") as run_builder:
            board.refresh_inputs()

        run_builder.assert_called_once_with(board.ETH_PROOF_GATE_BUILDER)

    def test_build_payload_ranks_eth_observe_first(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 7},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        research_board = {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"}
        offensive_board = {
            "experiment_protocol": {"primary_success": ["carry drag falls"]},
            "rows": [{"pilot": "ETHUSD M5 step5 Hungry Hippo rebuild", "status": "first_shadow_pilot"}],
        }
        eth_comparison = {
            "comparison_status": "blocked_until_control_normalized",
            "normalization_recommendation": {"recommended_control_step": 14.0},
        }
        eth_control_state = {
            "metadata": {"step": 14.0, "raw_close_alpha": 1.0},
            "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}},
        }
        eth_control_gate = {"summary": {"verdict": "continue_observation"}, "control_runtime": {}}
        reset_alerts = {"lanes_killed": 0, "reset_rate_limit": 6}
        authority_stack_text = ""
        fresh_window_text = ""
        closure_firewall_text = ""
        validated_theory_queue_text = ""
        gbp_closure_repair_compare = {}

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            research_board,
            offensive_board,
            eth_comparison,
            eth_control_state,
            eth_control_gate,
            reset_alerts,
            authority_stack_text,
            fresh_window_text,
            closure_firewall_text,
            validated_theory_queue_text,
            gbp_closure_repair_compare,
        )

        self.assertEqual(payload["rows"][0]["category"], "observe_now")
        self.assertEqual(payload["rows"][0]["action"], "keep_eth_m5_step14_control_running_as_the_single_proof_lane")
        self.assertEqual(payload["summary"]["eth_control_closes"], 11)
        self.assertEqual(payload["rows"][1]["action"], "disable_or_park_enabled_configs_that_fail_the_current_launch_contract")

    def test_build_payload_switches_top_action_when_eth_runtime_is_stale(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        research_board = {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"}
        offensive_board = {
            "experiment_protocol": {"primary_success": ["carry drag falls"]},
            "rows": [{"pilot": "ETHUSD M5 step5 Hungry Hippo rebuild", "status": "first_shadow_pilot"}],
        }
        eth_comparison = {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}}
        eth_control_state = {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}}
        eth_control_gate = {"summary": {"verdict": "blocked_by_stale_runtime"}, "control_runtime": {"heartbeat_at": "2026-04-15T15:40:25+00:00", "heartbeat_age_seconds": 1800, "runtime_stale": True}}
        reset_alerts = {"lanes_killed": 0, "reset_rate_limit": 6}
        authority_stack_text = "Queue consequence | `watch for fresh filesystem proof`, do not declare success or failure yet |"
        fresh_window_text = 'NAS100 should drop from "cleanest next expansion seam" language until it clears a fresh-window closure read. hold judgment until those surfaces refresh.'
        closure_firewall_text = "closure policy is overwhelming it"
        validated_theory_queue_text = "ETH M5 step5_v1 is DEAD: Registry says `enabled: true`, HH config says `enabled: false` ← CONTRADICTION"
        gbp_closure_repair_compare = {}

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            research_board,
            offensive_board,
            eth_comparison,
            eth_control_state,
            eth_control_gate,
            reset_alerts,
            authority_stack_text,
            fresh_window_text,
            closure_firewall_text,
            validated_theory_queue_text,
            gbp_closure_repair_compare,
        )

        self.assertEqual(payload["rows"][0]["category"], "verify_now")
        self.assertEqual(payload["rows"][0]["action"], "verify_or_restore_eth_m5_step14_control_runtime_before_treating_it_as_the_proof_lane")
        self.assertTrue(payload["rows"][0]["machine_truth"]["infra_surface_contradiction"])
        self.assertEqual(payload["rows"][1]["action"], "treat_gbpusd_alpha_half_as_bucket_diagnosis_before_any_promotion_or_default_story")
        self.assertEqual(payload["rows"][2]["action"], "wait_for_filesystem_confirmed_post_launch_proof_before_judging_btc_m15_sell_tight")
        self.assertEqual(payload["rows"][4]["action"], "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves")

    def test_build_payload_uses_portability_board_for_next_expansion_ranking(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        portability_board = {
            "summary": {
                "ready_for_shadow_discussion_symbols": [],
                "waiting_forward_proof_symbols": ["NAS100"],
                "missing_launch_contract_symbols": ["EURUSD"],
                "guardrail_blocked_symbols": ["BTCUSD", "ETHUSD"],
                "missing_policy_symbols": ["USDCAD"],
            },
            "rows": [
                {
                    "symbol": "NAS100",
                    "asset_class": "index",
                    "generalization_status": "portable_waiting_forward_proof",
                    "highest_leverage_gap": "forward_shadow_proof",
                    "deployment_verdict": "hard_block",
                    "guardrail_status": "promotable_now",
                    "surface_coverage_complete": True,
                    "launch_contract_count": 2,
                    "enabled_launch_contract_count": 0,
                    "note": "Canonical policy and at least one runnable contract exist; the gate is waiting on enough fresh forward proof.",
                }
            ],
        }

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}},
            {"summary": {"verdict": "blocked_by_stale_runtime"}, "control_runtime": {"heartbeat_at": "2026-04-15T15:40:25+00:00", "heartbeat_age_seconds": 1800, "runtime_stale": True}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            'NAS100 should drop from "cleanest next expansion seam" language until it clears a fresh-window closure read.',
            "",
            "",
            {},
            portability_board=portability_board,
        )

        portability_row = payload["rows"][4]
        self.assertEqual(portability_row["action"], "keep_nas100_as_the_leading_portable_forward_proof_candidate_but_not_a_clean_expansion_story_yet")
        self.assertEqual(portability_row["machine_truth"]["generalization_status"], "portable_waiting_forward_proof")
        self.assertTrue(portability_row["machine_truth"]["nas100_demoted_by_fresh_window"])
        self.assertEqual(portability_row["machine_truth"]["launch_contract_count"], 2)

    def test_build_payload_adds_balance_growth_unlock_ladder_when_policy_gap_board_exists(self) -> None:
        payload = board.build_payload(
            {
                "summary": {"blocking_enabled_config_count": 0},
                "rows": [
                    {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                    {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                    {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                    {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                ],
            },
            {
                "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD"]},
                "rows": [
                    {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                    {"symbol": "NAS100", "deployment_verdict": "hard_block", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                    {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                    {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
                ],
            },
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}},
            {"summary": {"verdict": "blocked_by_stale_runtime"}, "control_runtime": {"heartbeat_at": "2026-04-15T15:40:25+00:00", "heartbeat_age_seconds": 1800, "runtime_stale": True}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            'NAS100 should drop from "cleanest next expansion seam" language until it clears a fresh-window closure read.',
            "",
            "",
            {},
            portability_board={
                "summary": {
                    "family_portable_count": 19,
                    "surface_coverage_complete_count": 6,
                    "waiting_forward_proof_symbols": ["NAS100"],
                    "guardrail_blocked_symbols": ["BTCUSD", "ETHUSD", "GBPUSD"],
                },
                "rows": [
                    {
                        "symbol": "NAS100",
                        "asset_class": "index",
                        "generalization_status": "portable_waiting_forward_proof",
                        "highest_leverage_gap": "forward_shadow_proof",
                        "deployment_verdict": "hard_block",
                        "guardrail_status": "promotable_now",
                        "surface_coverage_complete": True,
                        "launch_contract_count": 2,
                        "enabled_launch_contract_count": 0,
                    }
                ],
            },
            policy_gap_board={
                "summary": {"missing_policy_symbol_count": 11},
                "rows": [
                    {"symbol": "USDCHF", "asset_class": "fx", "priority": "policy_seed_now"},
                    {"symbol": "USDCAD", "asset_class": "fx", "priority": "policy_seed_now"},
                    {"symbol": "USDJPY", "asset_class": "fx", "priority": "policy_seed_next"},
                    {"symbol": "XRPUSD", "asset_class": "crypto", "priority": "policy_seed_next"},
                ],
            },
        )

        doctrine_row = payload["rows"][5]
        self.assertEqual(doctrine_row["action"], "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout")
        self.assertEqual(doctrine_row["category"], "design_now")
        self.assertEqual(doctrine_row["machine_truth"]["lead_forward_proof_symbol"], "NAS100")
        self.assertEqual(doctrine_row["machine_truth"]["policy_seed_now_symbols"], ["USDCHF", "USDCAD"])
        self.assertEqual(doctrine_row["machine_truth"]["policy_seed_now_asset_class_counts"], {"fx": 2})
        self.assertTrue(doctrine_row["machine_truth"]["policy_seed_now_single_asset_class"])
        self.assertIn("balance-growth unlock ladder", payload["leadership_read"][3])
        self.assertIn("concentrated in fx (2)", payload["leadership_read"][3].lower())

    def test_build_payload_updates_unlock_ladder_prose_when_seed_now_focus_changes(self) -> None:
        payload = board.build_payload(
            {
                "summary": {"blocking_enabled_config_count": 0},
                "rows": [
                    {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                    {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                    {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                    {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                ],
            },
            {
                "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD"]},
                "rows": [
                    {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                    {"symbol": "NAS100", "deployment_verdict": "hard_block", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                    {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                    {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
                ],
            },
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}},
            {"summary": {"verdict": "blocked_by_stale_runtime"}, "control_runtime": {"heartbeat_at": "2026-04-15T15:40:25+00:00", "heartbeat_age_seconds": 1800, "runtime_stale": True}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            'NAS100 should drop from "cleanest next expansion seam" language until it clears a fresh-window closure read.',
            "",
            "",
            {},
            portability_board={
                "summary": {
                    "family_portable_count": 19,
                    "surface_coverage_complete_count": 8,
                    "waiting_forward_proof_symbols": ["NAS100"],
                    "guardrail_blocked_symbols": ["BTCUSD", "ETHUSD", "GBPUSD"],
                },
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "asset_class": "fx",
                        "generalization_status": "ready_for_shadow_discussion",
                        "highest_leverage_gap": "fresh_forward_proof",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "guardrail_status": "promotable_now",
                        "surface_coverage_complete": True,
                        "launch_contract_count": 1,
                        "enabled_launch_contract_count": 0,
                    },
                    {
                        "symbol": "NAS100",
                        "asset_class": "index",
                        "generalization_status": "portable_waiting_forward_proof",
                        "highest_leverage_gap": "forward_shadow_proof",
                        "deployment_verdict": "hard_block",
                        "guardrail_status": "promotable_now",
                        "surface_coverage_complete": True,
                        "launch_contract_count": 2,
                        "enabled_launch_contract_count": 0,
                    }
                ],
            },
            policy_gap_board={
                "summary": {"missing_policy_symbol_count": 8},
                "rows": [
                    {"symbol": "XRPUSD", "asset_class": "crypto", "priority": "policy_seed_now"},
                    {"symbol": "AUDUSD", "asset_class": "fx", "priority": "policy_seed_next"},
                ],
            },
        )

        doctrine_row = payload["rows"][5]
        self.assertIn("concentrated in crypto (1)", doctrine_row["rationale"].lower())
        self.assertIn("concentrated in crypto (1)", payload["leadership_read"][3].lower())

    def test_build_payload_prioritizes_surface_reconciliation_when_eth_gate_reports_surface_split(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        research_board = {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"}
        offensive_board = {
            "experiment_protocol": {"primary_success": ["carry drag falls"]},
            "rows": [{"pilot": "ETHUSD M5 step5 Hungry Hippo rebuild", "status": "first_shadow_pilot"}],
        }
        eth_comparison = {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}}
        eth_control_state = {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}}
        eth_control_gate = {
            "summary": {"verdict": "blocked_by_surface_alignment"},
            "control_runtime": {"heartbeat_at": "2026-04-15T15:40:25+00:00", "heartbeat_age_seconds": 1800, "runtime_stale": True},
            "infra_alignment": {
                "surface_alignment_blocked": True,
                "registry_lane_found": False,
                "config_enabled": False,
                "registry_enabled": True,
                "enabled_alignment_ok": False,
                "control_board_matches_launch_surface": False,
                "control_state_registered_launch_lane": False,
                "control_state_orphaned_from_registry": True,
                "declared_step_alignment_ok": False,
                "control_board_declared_step": 14.0,
                "step5_declared_step": 5.0,
            },
        }
        reset_alerts = {"lanes_killed": 0, "reset_rate_limit": 6}
        authority_stack_text = ""
        fresh_window_text = ""
        closure_firewall_text = ""
        validated_theory_queue_text = "ETH M5 step5_v1 is DEAD: Registry says `enabled: true`, HH config says `enabled: false` ← CONTRADICTION"
        gbp_closure_repair_compare = {}

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            research_board,
            offensive_board,
            eth_comparison,
            eth_control_state,
            eth_control_gate,
            reset_alerts,
            authority_stack_text,
            fresh_window_text,
            closure_firewall_text,
            validated_theory_queue_text,
            gbp_closure_repair_compare,
        )

        self.assertEqual(payload["rows"][0]["category"], "fix_now")
        self.assertEqual(payload["rows"][0]["action"], "register_eth_m5_step14_control_and_repoint_the_proof_board_to_the_same_lane")
        self.assertTrue(payload["rows"][0]["machine_truth"]["infra_surface_contradiction"])
        self.assertFalse(payload["rows"][0]["machine_truth"]["registry_lane_found"])
        self.assertFalse(payload["rows"][0]["machine_truth"]["enabled_alignment_ok"])
        self.assertFalse(payload["rows"][0]["machine_truth"]["control_board_matches_launch_surface"])
        self.assertFalse(payload["rows"][0]["machine_truth"]["control_state_registered_launch_lane"])
        self.assertTrue(payload["rows"][0]["machine_truth"]["control_state_orphaned_from_registry"])

    def test_build_payload_prioritizes_registered_lane_cutover_when_registry_is_aligned_but_proof_is_orphaned(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        research_board = {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"}
        offensive_board = {
            "experiment_protocol": {"primary_success": ["carry drag falls"]},
            "rows": [{"pilot": "ETHUSD M5 step5 Hungry Hippo rebuild", "status": "first_shadow_pilot"}],
        }
        eth_comparison = {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}}
        eth_control_state = {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}}
        eth_control_gate = {
            "summary": {"verdict": "blocked_by_surface_alignment"},
            "control_runtime": {"heartbeat_at": "2026-04-15T15:40:25+00:00", "heartbeat_age_seconds": 1800, "runtime_stale": True},
            "infra_alignment": {
                "surface_alignment_blocked": True,
                "registry_lane_found": True,
                "config_enabled": True,
                "registry_enabled": True,
                "enabled_alignment_ok": True,
                "control_board_matches_launch_surface": False,
                "control_state_registered_launch_lane": False,
                "control_state_orphaned_from_registry": True,
                "declared_step_alignment_ok": True,
                "control_board_declared_step": 14.0,
            },
        }
        gbp_closure_repair_compare = {}

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            research_board,
            offensive_board,
            eth_comparison,
            eth_control_state,
            eth_control_gate,
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "",
            "",
            gbp_closure_repair_compare,
        )

        self.assertEqual(payload["rows"][0]["action"], "retire_orphan_eth_m5_proof_artifact_and_restore_registered_step14_control_runtime")
        self.assertTrue(payload["rows"][0]["machine_truth"]["registry_lane_found"])
        self.assertFalse(payload["rows"][0]["machine_truth"]["control_board_matches_launch_surface"])
        self.assertTrue(payload["rows"][0]["machine_truth"]["control_state_orphaned_from_registry"])

    def test_build_payload_prioritizes_runtime_geometry_normalization_after_surface_reconciliation(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "ready_for_clean_control_vs_variant", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 1, "realized_net_usd": -15.75}}},
            {
                "summary": {"verdict": "blocked_by_control_normalization"},
                "control_runtime": {
                    "heartbeat_at": "2026-04-15T17:48:05+00:00",
                    "heartbeat_age_seconds": 7.3,
                    "runtime_stale": False,
                    "geometry_normalized": False,
                    "effective_buy_distance": 15.418145,
                    "effective_sell_distance": 0.14,
                    "buy_drift_ratio": 0.1013,
                    "sell_drift_ratio": 0.99,
                },
                "infra_alignment": {"surface_alignment_blocked": False, "registry_lane_found": True, "control_board_matches_launch_surface": True},
            },
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "",
            "",
            {},
        )

        self.assertEqual(payload["rows"][0]["action"], "normalize_eth_m5_step14_runtime_geometry_and_accumulate_honest_control_proof")
        self.assertEqual(payload["rows"][0]["category"], "fix_now")
        self.assertFalse(payload["rows"][0]["machine_truth"]["runtime_stale"])
        self.assertFalse(payload["rows"][0]["machine_truth"]["geometry_normalized"])
        self.assertEqual(payload["rows"][0]["machine_truth"]["eth_realized_net_usd"], -15.75)
        self.assertIn("heartbeat is fresh", payload["leadership_read"][1])

    def test_build_payload_uses_offensive_board_summary_first_pilot_label(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        research_board = {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"}
        offensive_board = {
            "summary": {"first_pilot": "ETHUSD M5 step14 normalized control"},
            "experiment_protocol": {"primary_success": ["carry drag falls"]},
            "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}],
        }
        eth_comparison = {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}}
        eth_control_state = {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}}
        eth_control_gate = {"summary": {"verdict": "continue_observation"}, "control_runtime": {}}
        reset_alerts = {"lanes_killed": 0, "reset_rate_limit": 6}

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            research_board,
            offensive_board,
            eth_comparison,
            eth_control_state,
            eth_control_gate,
            reset_alerts,
            "",
            "",
            "",
            "",
            {},
        )

        self.assertEqual(payload["rows"][3]["machine_truth"]["first_pilot_status"], "first_honest_pilot_after_control_restore")

    def test_build_payload_promotes_explicit_gbp_pair_launch_action(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}},
            {"summary": {"verdict": "blocked_by_surface_alignment"}, "control_runtime": {}, "infra_alignment": {"surface_alignment_blocked": True}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "",
            "",
            {
                "next_action": "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair",
                "paired_experiment_live": False,
                "baseline_present": True,
                "no_escape_present": False,
            },
        )

        self.assertEqual(payload["rows"][1]["action"], "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair")
        self.assertEqual(payload["rows"][1]["category"], "fix_now")
        self.assertFalse(payload["rows"][1]["machine_truth"]["gbp_closure_pair_live"])
        self.assertFalse(payload["rows"][1]["machine_truth"]["gbp_no_escape_present"])

    def test_build_payload_tracks_live_gbp_pair_as_evidence_collection_next_step(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }
        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}},
            {"summary": {"verdict": "blocked_by_surface_alignment"}, "control_runtime": {}, "infra_alignment": {"surface_alignment_blocked": True, "registry_lane_found": True, "control_board_matches_launch_surface": False}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "",
            "",
            {
                "next_action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape",
                "paired_experiment_live": True,
                "baseline_present": True,
                "no_escape_present": True,
            },
        )

        self.assertEqual(payload["rows"][1]["action"], "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape")
        self.assertEqual(payload["rows"][1]["category"], "watch_now")
        self.assertTrue(payload["rows"][1]["machine_truth"]["gbp_closure_pair_live"])
        self.assertTrue(payload["rows"][1]["machine_truth"]["gbp_no_escape_present"])

    def test_build_payload_keeps_gbp_pair_truth_visible_after_pair_is_live(self) -> None:
        launch_safety = {
            "summary": {"blocking_enabled_config_count": 0},
            "rows": [
                {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "fail", "hard_fail_reasons": ["missing_escape_hatch_flag"]},
                {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
            ],
        }
        deployment_gate = {
            "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
            "rows": [
                {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
            ],
        }

        payload = board.build_payload(
            launch_safety,
            deployment_gate,
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}}},
            {"summary": {"verdict": "blocked_by_surface_alignment"}, "control_runtime": {}, "infra_alignment": {"surface_alignment_blocked": True, "registry_lane_found": True, "control_board_matches_launch_surface": False}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "closure policy is overwhelming it",
            "",
            {
                "next_action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape",
                "paired_experiment_live": True,
                "baseline_present": True,
                "no_escape_present": True,
            },
        )

        self.assertEqual(payload["rows"][1]["action"], "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape")
        self.assertEqual(payload["rows"][1]["category"], "watch_now")
        self.assertTrue(payload["rows"][1]["machine_truth"]["gbp_closure_pair_live"])
        self.assertTrue(payload["rows"][1]["machine_truth"]["gbp_no_escape_present"])
        self.assertEqual(
            payload["rows"][1]["machine_truth"]["gbp_compare_next_action"],
            "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape",
        )

    def test_render_mentions_do_not_promote(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "leadership_read": ["one"],
            "summary": {"action_count": 1, "top_priority_action": "a", "blocking_enabled_config_count": 7, "eth_control_closes": 11, "eth_control_realized_net_usd": 19.53},
            "rows": [
                {
                    "priority": 1,
                    "category": "do_not_promote",
                    "action": "do_not_graduate",
                    "rationale": "r",
                    "machine_truth": {"x": 1},
                    "advance_when": "a",
                    "kill_when": "k",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Hungry Hippo Next Action Board", markdown)
        self.assertIn("do_not_promote", markdown)
        self.assertIn("do_not_graduate", markdown)

    def test_build_payload_uses_live_btc_v2_sample_instead_of_waiting_for_files(self) -> None:
        payload = board.build_payload(
            {
                "summary": {"blocking_enabled_config_count": 0},
                "rows": [
                    {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                    {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                    {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                    {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                ],
            },
            {
                "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD", "US30"]},
                "rows": [
                    {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 17},
                    {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                    {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                    {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
                ],
            },
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_control_restore"}]},
            {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 0, "realized_net_usd": 0.0}}},
            {"summary": {"verdict": "blocked_by_negative_expectancy"}, "control_runtime": {"runtime_stale": False}, "infra_alignment": {"registry_lane_found": True, "control_board_matches_launch_surface": True}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "",
            "",
            {"next_action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "paired_experiment_live": True, "baseline_present": True, "no_escape_present": True},
            {"enabled": True, "stale_after_seconds": 240, "hungry_hippo_metadata": {"guardrails": {"max_resets_per_close": 2.0, "max_resets_per_hour": 6}}},
            {"runner": {"started_at": "2026-04-15T17:59:57+00:00"}, "symbols": {"BTCUSD": {"realized_closes": 9, "realized_net_usd": -163.73, "anchor_resets": 11}}, "updated_at": "2026-04-15T18:10:29+00:00"},
            {"v2_close_mix": {"total_close_events": 9, "harvest_closes": 0, "escape_tier2_surgical_closes": 9, "harvest_share": 0.0, "close_mix_status": "zero_harvest_all_escape_so_far", "all_closes_escape_dominated": True}},
        )

        btc_row = payload["rows"][2]
        self.assertEqual(btc_row["action"], "continue_btc_m15_sell_tight_v2_forward_proof_and_watch_reset_behavior")
        self.assertTrue(btc_row["machine_truth"]["btc_forward_proof_started"])
        self.assertEqual(btc_row["machine_truth"]["btc_realized_closes"], 9)
        self.assertEqual(btc_row["machine_truth"]["btc_harvest_closes"], 0)
        self.assertEqual(btc_row["machine_truth"]["btc_escape_tier2_surgical_closes"], 9)
        self.assertEqual(btc_row["machine_truth"]["btc_close_mix_status"], "zero_harvest_all_escape_so_far")
        self.assertIn("zero harvest closes", btc_row["rationale"])
        self.assertIn("close_ticket harvest closes appear", btc_row["advance_when"])
        self.assertIn("first honest proof-accumulation job", payload["leadership_read"][1])
        self.assertIn("positive proof on the aligned ETH control arm", payload["rows"][3]["rationale"])
        self.assertIn("enough positive proof", payload["rows"][3]["advance_when"])

    def test_build_payload_turns_completed_negative_proof_into_eth_decision_fork_when_analysis_exists(self) -> None:
        payload = board.build_payload(
            {
                "summary": {"blocking_enabled_config_count": 0},
                "rows": [
                    {"config_path": "configs/hungry_hippo_ethusd_m5_step5_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_btcusd_m15_sell_tight_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                    {"config_path": "configs/hungry_hippo_btcusd_m5_step200_shadow.json", "verdict": "fail", "hard_fail_reasons": ["crypto_runner_has_fx_only_escape_flags"]},
                    {"config_path": "configs/hungry_hippo_gbpusd_deploy.json", "verdict": "fail", "hard_fail_reasons": ["fx_step_below_floor"]},
                    {"config_path": "configs/hungry_hippo_xauusd_consolidation_shadow.json", "verdict": "fail", "hard_fail_reasons": ["alpha_below_floor"]},
                    {"config_path": "configs/hungry_hippo_nas100_m15_breakout_buy_shadow.json", "verdict": "research_only", "hard_fail_reasons": []},
                ],
            },
            {
                "summary": {"hard_block_symbols": ["BTCUSD", "ETHUSD"]},
                "rows": [
                    {"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST", "proof_closes": 36},
                    {"symbol": "NAS100", "deployment_verdict": "manual_review", "proof_closes": 36, "ratio_to_atr": 0.3, "guardrail_status": "promotable_now"},
                    {"symbol": "GBPUSD", "deployment_verdict": "manual_review", "guardrail_status": "contradiction", "proof_closes": 111, "ratio_to_atr": 0.333},
                    {"symbol": "BTCUSD", "deployment_verdict": "hard_block"},
                ],
            },
            {"best_overall_contribution": "reconcile_gbpusd_alpha_half_live_path"},
            {"summary": {"first_pilot": "ETHUSD M5 step14 normalized control"}, "experiment_protocol": {"primary_success": ["carry drag falls"]}, "rows": [{"pilot": "ETHUSD M5 step14 normalized control", "status": "first_honest_pilot_after_positive_control_proof"}]},
            {"comparison_status": "ready_for_clean_control_vs_variant", "normalization_recommendation": {"recommended_control_step": 14.0}},
            {"metadata": {"step": 14.0, "raw_close_alpha": 1.0}, "symbols": {"ETHUSD": {"realized_closes": 36, "realized_net_usd": -314.29}}},
            {"summary": {"verdict": "blocked_by_negative_expectancy", "target_closes": 25, "avg_per_close": -8.7303}, "control_runtime": {"runtime_stale": False}, "infra_alignment": {"registry_lane_found": True, "control_board_matches_launch_surface": True}},
            {"lanes_killed": 0, "reset_rate_limit": 6},
            "",
            "",
            "",
            "",
            {"next_action": "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape", "paired_experiment_live": True, "baseline_present": True, "no_escape_present": True},
            {"enabled": True, "stale_after_seconds": 240, "hungry_hippo_metadata": {"guardrails": {"max_resets_per_close": 2.0, "max_resets_per_hour": 6}}},
            {"runner": {"started_at": "2026-04-15T17:59:57+00:00"}, "symbols": {"BTCUSD": {"realized_closes": 9, "realized_net_usd": -163.73, "anchor_resets": 11}}, "updated_at": "2026-04-15T18:10:29+00:00"},
            {"v2_close_mix": {"total_close_events": 9, "harvest_closes": 0, "escape_tier2_surgical_closes": 9, "harvest_share": 0.0, "close_mix_status": "zero_harvest_all_escape_so_far", "all_closes_escape_dominated": True}},
            board.parse_eth_step14_coefficient_analysis(
                """### Option A: Step ~$3.00
### Option B: Step ~$1.40
### Option C: Accept negative proof and kill lane
2. Run for 25+ closes minimum
"""
            ),
            {"runner": {"heartbeat_at": "2026-04-15T22:07:43+00:00"}, "symbols": {"ETHUSD": {"realized_closes": 24, "realized_net_usd": -167.11, "open_tickets": [{}, {}, {}]}}},
        )

        eth_row = payload["rows"][0]
        self.assertEqual(eth_row["action"], "decide_eth_step14_negative_proof_response_kill_or_launch_retuned_shadow")
        self.assertEqual(eth_row["category"], "decide_now")
        self.assertEqual(eth_row["machine_truth"]["recommended_retune_step_usd"], 3.0)
        self.assertEqual(eth_row["machine_truth"]["recommended_min_shadow_closes"], 25)
        self.assertEqual(eth_row["machine_truth"]["step3p0_closes"], 24)
        self.assertEqual(eth_row["machine_truth"]["step3p0_net"], -167.11)
        self.assertIn("kill the disproved step14 control", eth_row["advance_when"])
        self.assertIn("decision fork", payload["leadership_read"][1])
        self.assertIn("kill-or-retune around the published ~$3.00 shadow candidate", payload["leadership_read"][2])

    def test_parse_eth_step14_coefficient_analysis_accepts_heading_only_options(self) -> None:
        parsed = board.parse_eth_step14_coefficient_analysis(
            """### Option A: Step ~$3.00
### Option B: Step ~$1.40
### Option C: Accept negative proof and kill lane

## Recommendation

1. Deploy as NEW shadow lane
2. Run for 25+ closes minimum
"""
        )

        self.assertEqual(parsed["recommended_option"], "Option A")
        self.assertEqual(parsed["recommended_step_usd"], 3.0)
        self.assertEqual(parsed["alternate_step_usd"], 1.4)
        self.assertEqual(parsed["minimum_proof_closes"], 25)
        self.assertTrue(parsed["kill_option_available"])


if __name__ == "__main__":
    unittest.main()
