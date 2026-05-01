#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_graduation_rubric_board as board


class BuildGraduationRubricBoardTests(unittest.TestCase):
    def test_build_payload_maps_current_candidates_into_rubric(self) -> None:
        controller_priors = {
            "symbol_priors": {
                "GBPUSD": {"close_alpha_prior": 0.5},
            }
        }
        profit_board = {
            "rows": [
                {"theory": "eth_m5_no_session_gate_harvest_rebuild", "stage": "tested_theory_waiting_for_clean_control", "machine_truth": {"control_verdict": "blocked_by_stale_runtime", "control_realized_closes": 11, "control_avg_per_close": 1.7755}},
                {"theory": "btc_m15_downtrend_sell_tight_shape", "stage": "shadow_config_reconciled_waiting_forward_proof"},
                {"theory": "btc_m5_step200_salvage_probe", "stage": "shadow_probe_only"},
                {"theory": "fx_alpha_half_universal_prior", "stage": "validated_live_prior"},
                {"theory": "index_asymmetry_family_prior", "stage": "forward_validating"},
            ]
        }
        readiness_board = {
            "rows": [
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {"proof_closes": 111, "harvest_close_ticket_usd": 153.71, "escape_tier0_offensive_usd": -2074.07, "forced_unwind_usd": -572.37}},
                {"candidate": "ETHUSD M5 step14 normalized control", "readiness": "control_restore_required", "evidence": {"realized_closes": 11}},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "readiness": "shadow_reconciled_waiting_forward_proof", "evidence": {"reconciliation_status": "reconciled_and_ready_to_launch", "validation_status": "shadow_config_reconciled_2026_04_15"}},
                {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {"shadow_realized_closes": 2, "shadow_avg_per_close": 69.98, "launch_verdict": "fail"}},
                {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {"proof_closes": 36, "launch_verdict": "research_only", "guardrail_status": "promotable_now", "deployment_gate_verdict": "manual_review"}},
            ]
        }
        promotion_gate = {
            "rows": [
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live"},
                {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control", "promotion_verdict": "restore_control_then_validate_shadow"},
                {"candidate": "BTCUSD M15 sell-tight downtrend shape", "current_stage": "shadow_config_reconciled_waiting_forward_proof", "promotion_verdict": "reconcile_shadow_then_judge"},
                {"candidate": "NAS100 asym breakout family lane", "current_stage": "research_only_shadow_candidate"},
                {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample", "promotion_verdict": "too_early_for_live"},
            ]
        }

        payload = board.build_payload(controller_priors, profit_board, readiness_board, promotion_gate)

        self.assertEqual(payload["rows"][0]["candidate"], "ETHUSD M5 step14 normalized control")
        self.assertEqual(payload["rows"][0]["next_gate"], "shadow_to_validated_shadow")
        self.assertEqual(payload["rows"][1]["current_stage"], "shadow_reconciled_waiting_forward_proof")
        self.assertEqual(payload["rows"][2]["next_gate"], "validated_shadow_to_live")
        self.assertEqual(payload["rows"][-1]["family"], "crypto_salvage_probe")

    def test_render_markdown_mentions_new_thresholds(self) -> None:
        payload = {
            "generated_at": "2026-04-15T03:00:00+00:00",
            "leadership_read": ["one"],
            "stage_thresholds": {"shadow_to_validated_shadow": {"required": ["fresh evidence"]}},
            "rows": [
                {
                    "candidate": "ETHUSD M5 step14 normalized control",
                    "family": "crypto_m5_rebuild",
                    "current_stage": "tested_theory_waiting_for_clean_control",
                    "next_gate": "shadow_to_validated_shadow",
                    "candidate_rubric": {"required_forward_closes": 25},
                    "current_metrics": {"control_avg_per_close": 1.7755},
                    "gap_to_next_gate": "stale runtime",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Graduation Rubric Board", markdown)
        self.assertIn("shadow_to_validated_shadow", markdown)
        self.assertIn("ETHUSD M5 step14 normalized control", markdown)

    def test_build_payload_prefers_current_eth_readiness_and_gate_over_stale_theory_metrics(self) -> None:
        payload = board.build_payload(
            {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}},
            {
                "rows": [
                    {"theory": "eth_m5_no_session_gate_harvest_rebuild", "stage": "tested_theory_waiting_for_clean_control", "machine_truth": {"control_verdict": "blocked_by_surface_alignment", "control_realized_closes": 11, "control_avg_per_close": 1.7755}},
                    {"theory": "btc_m15_downtrend_sell_tight_shape", "stage": "shadow_config_reconciled_waiting_forward_proof"},
                    {"theory": "btc_m5_step200_salvage_probe", "stage": "shadow_probe_only"},
                    {"theory": "fx_alpha_half_universal_prior", "stage": "validated_live_prior"},
                    {"theory": "index_asymmetry_family_prior", "stage": "forward_validating"},
                ]
            },
            {
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {"proof_closes": 0, "harvest_close_ticket_usd": 153.71, "escape_tier0_offensive_usd": -2074.07, "forced_unwind_usd": -572.37}},
                    {"candidate": "ETHUSD M5 step14 normalized control", "readiness": "control_restore_required", "evidence": {"gate_verdict": "blocked_by_control_normalization", "realized_closes": 1, "avg_per_close": -15.75}},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "readiness": "shadow_reconciled_waiting_forward_proof", "evidence": {"reconciliation_status": "reconciled_and_ready_to_launch", "validation_status": "shadow_config_reconciled_2026_04_15"}},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {"shadow_realized_closes": 2, "shadow_avg_per_close": 69.98, "launch_verdict": "fail"}},
                    {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {"proof_closes": 36, "launch_verdict": "research_only", "guardrail_status": "promotable_now", "deployment_gate_verdict": "manual_review"}},
                ]
            },
            {
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live"},
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control", "promotion_verdict": "restore_control_then_validate_shadow", "blocking_issue": "blocked_by_control_normalization"},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "current_stage": "shadow_config_reconciled_waiting_forward_proof", "promotion_verdict": "reconcile_shadow_then_judge"},
                    {"candidate": "NAS100 asym breakout family lane", "current_stage": "research_only_shadow_candidate"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample", "promotion_verdict": "too_early_for_live"},
                ]
            },
        )

        eth_row = payload["rows"][0]
        self.assertEqual(eth_row["current_metrics"]["control_verdict"], "blocked_by_control_normalization")
        self.assertEqual(eth_row["current_metrics"]["control_realized_closes"], 1)
        self.assertEqual(eth_row["current_metrics"]["control_avg_per_close"], -15.75)
        self.assertIn("registered step14 lane is now the judged lane", eth_row["gap_to_next_gate"])

    def test_btc_rubric_uses_explicit_reset_guardrails_when_ready_evidence_has_them(self) -> None:
        payload = board.build_payload(
            {"symbol_priors": {"GBPUSD": {"close_alpha_prior": 0.5}}},
            {
                "rows": [
                    {"theory": "eth_m5_no_session_gate_harvest_rebuild", "stage": "tested_theory_waiting_for_clean_control", "machine_truth": {}},
                    {"theory": "btc_m15_downtrend_sell_tight_shape", "stage": "shadow_config_reconciled_waiting_forward_proof"},
                    {"theory": "btc_m5_step200_salvage_probe", "stage": "shadow_probe_only"},
                    {"theory": "fx_alpha_half_universal_prior", "stage": "validated_live_prior"},
                    {"theory": "index_asymmetry_family_prior", "stage": "forward_validating"},
                ]
            },
            {
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "readiness": "closure_policy_diagnosis_before_live", "evidence": {}},
                    {"candidate": "ETHUSD M5 step14 normalized control", "readiness": "control_restore_required", "evidence": {}},
                    {
                        "candidate": "BTCUSD M15 sell-tight downtrend shape",
                        "readiness": "shadow_forward_sample_running",
                        "evidence": {
                            "reconciliation_status": "reconciled_and_ready_to_launch",
                            "validation_status": "v2_retuned_2026_04_15",
                            "max_resets_per_hour": 6.0,
                            "max_resets_per_close": 2.0,
                        },
                    },
                    {"candidate": "BTCUSD M5 step200 salvage probe", "readiness": "shadow_probe_ready_low_sample", "evidence": {}},
                    {"candidate": "NAS100 asym breakout family lane", "readiness": "research_only_shadow_candidate", "evidence": {}},
                ]
            },
            {
                "rows": [
                    {"candidate": "GBPUSD alpha=0.5 FX harvest path", "current_stage": "closure_policy_diagnosis_before_live"},
                    {"candidate": "ETHUSD M5 step14 normalized control", "current_stage": "tested_theory_waiting_for_clean_control"},
                    {"candidate": "BTCUSD M15 sell-tight downtrend shape", "current_stage": "shadow_forward_sample_running", "promotion_verdict": "collect_forward_proof_then_judge"},
                    {"candidate": "NAS100 asym breakout family lane", "current_stage": "research_only_shadow_candidate"},
                    {"candidate": "BTCUSD M5 step200 salvage probe", "current_stage": "shadow_probe_ready_low_sample"},
                ]
            },
        )

        btc_row = payload["rows"][1]
        self.assertEqual(btc_row["candidate_rubric"]["required_reset_behavior"], "<=6.0/hour and <=2.0 resets/close")


if __name__ == "__main__":
    unittest.main()
