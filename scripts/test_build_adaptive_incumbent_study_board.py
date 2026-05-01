from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_incumbent_study_board as board


class BuildAdaptiveIncumbentStudyBoardTests(unittest.TestCase):
    def test_build_payload_classifies_current_study_gaps(self) -> None:
        payload = board.build_payload(
            seat_board={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "asset_class": "crypto",
                        "seat_verdict": "contested_provisional_live_seat",
                        "current_live_holder_lane": "live_btcusd_m15_warp_941781",
                        "current_live_holder_evidence_basis": "carry_weighted_live",
                        "current_live_holder_booked_usd": 1248.75,
                        "current_live_holder_close_count": 277,
                        "current_live_holder_operator_posture": "require_fresh_forward_sample",
                        "best_challenger_lane": "shadow_btcusd_m15_warp_restore_v1",
                        "best_challenger_family": "adaptive_shadow",
                        "best_challenger_runtime_status": "hold_runtime_repair_candidate",
                        "why": "btc why",
                    },
                    {
                        "symbol": "GBPUSD",
                        "asset_class": "fx",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "current_live_holder_lane": "live_rearm_941777",
                        "current_live_holder_evidence_basis": "graduated_live_reference",
                        "current_live_holder_booked_usd": 724.43,
                        "current_live_holder_close_count": 320,
                        "current_live_holder_operator_posture": "keep_live_reference",
                        "best_challenger_family": "fx_shadow",
                        "best_challenger_runtime_status": "waiting_good_session_window",
                        "why": "gbp why",
                    },
                    {
                        "symbol": "EURUSD",
                        "asset_class": "fx",
                        "seat_verdict": "defended_but_contested_live_seat",
                        "current_live_holder_lane": "live_rearm_941777",
                        "current_live_holder_evidence_basis": "graduated_live_reference",
                        "current_live_holder_booked_usd": 724.43,
                        "current_live_holder_close_count": 320,
                        "current_live_holder_operator_posture": "keep_live_reference",
                        "best_challenger_family": "fx_shadow",
                        "best_challenger_runtime_status": "waiting_good_session_window",
                        "why": "eur why",
                    },
                    {
                        "symbol": "NZDUSD",
                        "asset_class": "fx",
                        "seat_verdict": "provisional_live_seat",
                        "current_live_holder_lane": "live_momentum_alpha50_941778",
                        "current_live_holder_evidence_basis": "carry_weighted_live",
                        "current_live_holder_booked_usd": 24.91,
                        "current_live_holder_close_count": 188,
                        "current_live_holder_operator_posture": "require_fresh_forward_sample",
                        "best_challenger_lane": "shadow_nzdusd_m15_asym",
                        "best_challenger_family": "adaptive_shadow",
                        "best_challenger_runtime_status": "already_running_monitor_only",
                        "why": "nzd why",
                    },
                    {
                        "symbol": "USDJPY",
                        "asset_class": "fx",
                        "seat_verdict": "no_live_seat",
                        "current_live_holder_lane": "",
                        "current_live_holder_evidence_basis": "",
                        "current_live_holder_booked_usd": 0.0,
                        "current_live_holder_close_count": 0,
                        "current_live_holder_operator_posture": "",
                        "best_challenger_lane": "shadow_usdjpy_gap2",
                        "best_challenger_family": "adaptive_shadow",
                        "best_challenger_runtime_status": "hold_disabled_proof_candidate",
                        "why": "jpy why",
                    },
                ]
            },
            proof_board={
                "rows": [
                    {"symbol": "BTCUSD", "stage": "shadow_ready", "recommended_shape_id": "btcusd_regime_rangeatr_v1", "family": "raw", "profit_mode": "guarded_toxic_flow", "objective_read": "btc objective", "why": "btc proof"},
                    {"symbol": "ETHUSD", "stage": "probation", "recommended_shape_id": "ethusd_regime_rangeatr_v1", "family": "raw", "profit_mode": "trend_harvest", "objective_read": "eth objective", "why": "eth proof"},
                    {"symbol": "EURUSD", "stage": "research_only", "recommended_shape_id": "eurusd_mixed_floor_v1", "family": "raw", "profit_mode": "balanced_harvest", "objective_read": "eur objective", "why": "eur proof"},
                    {"symbol": "GBPUSD", "stage": "shadow_ready", "recommended_shape_id": "gbpusd_trend_harvest_v1", "family": "raw", "profit_mode": "trend_harvest", "objective_read": "gbp objective", "why": "gbp proof"},
                    {"symbol": "NZDUSD", "stage": "research_only", "recommended_shape_id": "nzdusd_asym_probe_v1", "family": "raw", "profit_mode": "trend_harvest", "objective_read": "nzd objective", "why": "nzd proof"},
                    {"symbol": "USDJPY", "stage": "bounded_proof_pending", "recommended_shape_id": "usdjpy_bounded_survival_v1", "family": "bounded", "profit_mode": "friction_survivor", "objective_read": "jpy objective", "why": "jpy proof"},
                ]
            },
            acceptance_board={
                "candidates": [
                    {
                        "candidate_id": "btc_restore_comparison_shadow",
                        "symbol": "BTCUSD",
                        "priority": 1,
                        "verdict": "shadow_ready",
                        "queue_status": "blocked",
                        "candidate_read": "btc candidate",
                        "machine_truth": {"recommended_branch_launch_status": "hold_runtime_repair_candidate"},
                    },
                    {
                        "candidate_id": "usdjpy_bounded_proof_refresh",
                        "symbol": "USDJPY",
                        "priority": 2,
                        "verdict": "research_only",
                        "queue_status": "",
                        "candidate_read": "jpy candidate",
                        "machine_truth": {},
                    },
                ]
            },
            controller_priors={
                "symbol_priors": {
                    "BTCUSD": {"controller_role": "crypto_split_baseline", "promotion_action": "hold_until_buy_realign", "controller_read": "btc prior"},
                    "ETHUSD": {"controller_role": "crypto_m5_rebuild_candidate", "promotion_action": "unblock_guardrails_first", "controller_read": "eth prior"},
                    "EURUSD": {"controller_role": "fx_alpha_half_survivor", "controller_read": "eur prior"},
                    "GBPUSD": {"controller_role": "fx_alpha_half_survivor", "controller_read": "gbp prior"},
                    "NZDUSD": {"controller_role": "fx_probe", "controller_read": "nzd prior"},
                    "USDJPY": {"controller_role": "bounded_candidate", "controller_read": "jpy prior"},
                    "NAS100": {"controller_role": "index_asym_breakout_candidate", "promotion_action": "wait_for_session_window", "controller_read": "nas prior"},
                    "US30": {"controller_role": "index_asym_candidate", "promotion_action": "unblock_guardrails_first", "controller_read": "us30 prior"},
                }
            },
            perfection_scorecard={
                "summary": {"total_score": 8, "max_score": 14, "overall_verdict": "instrumented_but_not_yet_perfect"}
            },
            branch_decision={
                "rows": [
                    {
                        "branch_id": "launch_restore_comparison_shadow",
                        "launch_status": "already_running_monitor_only",
                    }
                ]
            },
            btc_runtime_audit={
                "runtime_lane": {
                    "runner_session_trade_realized_usd": 0.0,
                    "runner_session_trade_closes": 0,
                    "pre_start_state_carry_realized_usd": -17.77,
                },
                "runtime_objective_context": {
                    "close_conversion_pressure": True,
                    "objective_read": "Monetization pressure active.",
                },
            },
            btc_adaptive_plan={
                "controller_recommendation": {
                    "recommended_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                }
            },
            btc_restore_board={
                "restore_candidate": {
                    "lane": "shadow_btcusd_m15_warp_restore_v1",
                }
            },
            booked_breakdown={
                "shadow_lattice": {
                    "rows": [
                        {
                            "lane": "shadow_btcusd_m15_warp_restore_v1",
                            "clean_forward_delta_usd": -0.8,
                            "booked_usd": -228.6,
                            "close_count": 13,
                            "notes": "clean_forward_since_repair=-0.8000/0c",
                        }
                    ]
                }
            },
            packet_board={
                "rows": [
                    {
                        "packet_id": "btc_restore_comparison_shadow",
                        "lane_name": "shadow_btcusd_m15_warp_restore_v1",
                        "action_status": "hold_runtime_repair_candidate",
                    },
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                        "action_status": "hold_launch_packet_defined_not_started",
                    },
                    {
                        "packet_id": "usdjpy_bounded_forward_proof",
                        "lane_name": "shadow_usdjpy_gap2",
                        "action_status": "hold_disabled_proof_candidate",
                    },
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["study_ready_symbols"], [])
        self.assertEqual(summary["blocked_symbols"], ["BTCUSD", "GBPUSD"])
        self.assertEqual(summary["research_only_symbols"], ["EURUSD", "NZDUSD"])
        self.assertEqual(summary["adaptive_without_incumbent_symbols"], ["ETHUSD", "USDJPY"])
        self.assertEqual(summary["prior_only_symbols"], ["NAS100", "US30"])
        self.assertEqual(summary["family_coverage"]["commodity"], "missing")
        self.assertEqual(summary["family_coverage"]["fx"], "blocked_candidate_present")
        self.assertEqual(summary["adaptive_profit_modes"]["BTCUSD"], "guarded_toxic_flow")
        self.assertEqual(summary["btc_max_profit_contract"]["adaptive_shape_id"], "btcusd_rangeatr_cash_harvest_v1")
        self.assertEqual(summary["btc_max_profit_contract"]["verdict"], "adaptive_candidate_defined_but_unproven")

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["BTCUSD"]["study_status"], "blocked_runtime_or_launch_gap")
        self.assertEqual(indexed["BTCUSD"]["adaptive_profit_mode"], "guarded_toxic_flow")
        self.assertEqual(indexed["BTCUSD"]["btc_max_profit_comparison"]["restore_lane"], "shadow_btcusd_m15_warp_restore_v1")
        self.assertTrue(indexed["BTCUSD"]["btc_max_profit_comparison"]["adaptive_close_conversion_pressure"])
        self.assertEqual(indexed["GBPUSD"]["study_status"], "blocked_runtime_or_launch_gap")
        self.assertEqual(indexed["GBPUSD"]["adaptive_runtime_status"], "hold_launch_packet_defined_not_started")
        self.assertEqual(indexed["GBPUSD"]["adaptive_lane"], "shadow_gbpusd_m15_trend_harvest_v1")
        self.assertEqual(indexed["EURUSD"]["study_status"], "research_only_adaptive_candidate")
        self.assertEqual(indexed["ETHUSD"]["study_status"], "adaptive_candidate_without_incumbent")
        self.assertEqual(indexed["NAS100"]["study_status"], "prior_only_family_gap")

    def test_render_markdown_mentions_family_coverage_and_rows(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "comparable_symbols": ["BTCUSD"],
                    "study_ready_symbols": [],
                    "blocked_symbols": ["BTCUSD"],
                    "research_only_symbols": [],
                    "adaptive_without_incumbent_symbols": [],
                    "prior_only_symbols": ["NAS100"],
                    "adaptive_profit_modes": {"BTCUSD": "guarded_toxic_flow"},
                    "btc_max_profit_contract": {
                        "verdict": "adaptive_candidate_defined_but_unproven",
                        "restore_lane": "shadow_btcusd_m15_warp_restore_v1",
                        "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                        "score_gap": -1,
                    },
                    "family_coverage": {"crypto": "blocked_candidate_present", "commodity": "missing"},
                    "adaptive_program_score": {"total_score": 8, "max_score": 14, "overall_verdict": "instrumented_but_not_yet_perfect"},
                },
                "leadership_read": ["one"],
                "family_coverage": [
                    {"family": "crypto", "verdict": "blocked_candidate_present", "symbols": ["BTCUSD"], "read": "blocked"},
                    {"family": "commodity", "verdict": "missing", "symbols": [], "read": "missing"},
                ],
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "asset_class": "crypto",
                        "incumbent_lane": "live_btcusd_m15_warp_941781",
                        "incumbent_evidence_basis": "carry_weighted_live",
                        "incumbent_booked_usd": 1248.75,
                        "adaptive_shape_id": "btcusd_regime_rangeatr_v1",
                        "adaptive_profit_mode": "guarded_toxic_flow",
                        "adaptive_profit_mode_read": "profit mode read",
                        "adaptive_objective_read": "objective read",
                        "adaptive_candidate_verdict": "shadow_ready",
                        "adaptive_runtime_status": "hold_runtime_repair_candidate",
                        "btc_max_profit_comparison": {
                            "verdict": "adaptive_candidate_defined_but_unproven",
                            "restore_score": -2,
                            "adaptive_score": -1,
                            "score_gap": 1,
                            "restore_lane": "shadow_btcusd_m15_warp_restore_v1",
                            "restore_basis": "clean_forward_delta_usd",
                            "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                            "read": "btc comparison",
                        },
                        "study_status": "blocked_runtime_or_launch_gap",
                        "study_ready": False,
                        "prior_role": "crypto_split_baseline",
                        "why": "blocked",
                        "incumbent_read": "incumbent",
                        "adaptive_read": "adaptive",
                        "prior_read": "prior",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive Incumbent Study Board", markdown)
        self.assertIn("Doctrine Family Coverage", markdown)
        self.assertIn("blocked_runtime_or_launch_gap", markdown)
        self.assertIn("commodity", markdown)
        self.assertIn("guarded_toxic_flow", markdown)
        self.assertIn("btcusd_rangeatr_cash_harvest_v1", markdown)
        self.assertIn("btc_max_profit_verdict", markdown)


if __name__ == "__main__":
    unittest.main()
