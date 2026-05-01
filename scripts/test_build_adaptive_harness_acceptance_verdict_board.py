from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_harness_acceptance_verdict_board as board


class BuildAdaptiveHarnessAcceptanceVerdictBoardTests(unittest.TestCase):
    def test_build_payload_grades_current_candidates_honestly(self) -> None:
        payload = board.build_payload(
            adaptive_queue={
                "summary": {"highest_priority_ready_task_id": "btc_restore_comparison_shadow"},
                "tasks": [
                    {
                        "task_id": "btc_restore_comparison_shadow",
                        "priority": 1,
                        "status": "ready",
                        "lane": "shadow crypto",
                        "title": "Launch the BTC M15 warp restore comparison shadow",
                        "why": "restore first",
                        "runtime_overlays": ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"],
                        "runtime_overlay_read": "guard opens and collapse burst risk",
                        "runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                        "runtime_obligation_read": "guard opens and collapse burst risk",
                    },
                    {"task_id": "btc_true_adaptive_candidate", "priority": 2, "status": "blocked", "lane": "shadow crypto", "title": "Define and build the true downtrend-aware adaptive BTC candidate", "why": "restore should land first", "allowed_inputs": ["btcusd_m15_bounce_down_v1"], "blocked_by": ["restore_comparison_shadow_should_land_first"]},
                    {"task_id": "btc_parked_artifact_review", "priority": 3, "status": "completed", "lane": "shadow crypto", "title": "Keep the parked BTC adaptive artifact in hold/manual-review only", "why": "historical only"},
                    {"task_id": "gbpusd_adaptive_comparison_packet", "priority": 4, "status": "ready", "lane": "shadow fx", "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat", "why": "trend-harvest comparison packet", "allowed_inputs": ["gbpusd_trend_harvest_v1"]},
                    {"task_id": "usdjpy_bounded_forward_proof", "priority": 6, "status": "ready", "lane": "runtime proof", "title": "Run fresh bounded proof", "why": "proof refresh", "allowed_inputs": ["usdjpy_bounded_survival_v1"]},
                ],
            },
            proof_board={
                "generated_at": "2026-04-16T05:34:05Z",
                "blockers": [{"blocker_id": "bounded_close_style_runtime_fault", "active": False}],
                "rows": [
                    {"symbol": "BTCUSD", "stage": "shadow_ready", "recommended_shape_id": "btcusd_regime_rangeatr_v1"},
                    {"symbol": "GBPUSD", "stage": "shadow_ready", "recommended_shape_id": "gbpusd_trend_harvest_v1", "profit_mode": "trend_harvest"},
                    {"symbol": "USDJPY", "stage": "bounded_proof_pending", "source_stage": "blocked_runtime", "family": "bounded", "recommended_shape_id": "usdjpy_bounded_survival_v1"},
                ],
            },
            formula_coverage={
                "generated_at": "2026-04-16T05:27:42Z",
                "rows": [
                    {"symbol": "BTCUSD", "verdict": "true_range_atr_ready", "missing_fields": []},
                    {"symbol": "USDJPY", "verdict": "atr_ready", "missing_fields": []},
                ],
            },
            btc_branch_decision={
                "generated_at": "2026-04-16T05:34:05Z",
                "summary": {"recommended_branch_id": "launch_restore_comparison_shadow", "doctrine_target_branch_id": "define_true_adaptive_candidate_then_build"},
                "rows": [
                    {"branch_id": "hold_parked_artifact_only", "status": "not_next_action"},
                    {"branch_id": "launch_restore_comparison_shadow", "status": "recommended_next_action"},
                    {"branch_id": "define_true_adaptive_candidate_then_build", "status": "doctrine_target_not_first_build", "execution_read": "manual_review_shadow_candidate"},
                ],
            },
            btc_runtime_audit={
                "status": "runtime_present_manual_review_required",
                "runtime_lane": {
                    "runner_session_trade_closes": 0,
                    "runner_session_trade_realized_usd": 0.0,
                    "pre_start_state_carry_closes": 1,
                    "pre_start_state_carry_realized_usd": -17.77,
                    "watchdog_status": "stale",
                    "direct_live": True,
                    "max_open_per_side": 6,
                    "last_trade_event_at": "2026-04-14T22:03:17Z",
                },
                "checks": [
                    {"check_id": "controller_step_mode", "status": "warn"},
                    {"check_id": "controller_alpha", "status": "warn"},
                    {"check_id": "design_asymmetry", "status": "warn"},
                    {"check_id": "runtime_direct_live", "status": "warn"},
                    {"check_id": "controller_max_open", "status": "warn"},
                    {"check_id": "design_max_open", "status": "pass"},
                ],
            },
            btc_restore_board={"restore_candidate": {"lane": "shadow_btcusd_m15_warp_restore_v1"}},
            btc_runner_plan={
                "status": "ready",
                "proposed_lane_name": "shadow_btcusd_m15_adaptive_v1",
                "adaptive_step_plan": {"kind": "range_atr_formula"},
                "step_review": {"review_read": "Judge against design target first."},
            },
            controller_priors={
                "global_policy": {"graduation_funnel": {"shadow_to_live": "requires proof"}},
                "symbol_priors": {"BTCUSD": {"controller_role": "crypto_split_baseline", "promotion_action": "hold_until_buy_realign"}},
            },
            incumbent_study={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "btc_max_profit_comparison": {
                            "verdict": "adaptive_candidate_defined_but_unproven",
                            "restore_lane": "shadow_btcusd_m15_warp_restore_v1",
                            "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                            "restore_score": -2,
                            "adaptive_score": -1,
                            "adaptive_runner_session_realized_usd": 0.0,
                            "adaptive_runner_session_close_count": 0,
                            "adaptive_pre_start_carry_realized_usd": -17.77,
                            "read": "btc contract read",
                        },
                    },
                    {
                        "symbol": "GBPUSD",
                        "study_status": "study_ready",
                        "adaptive_shape_id": "gbpusd_trend_harvest_v1",
                        "adaptive_profit_mode": "trend_harvest",
                        "incumbent_lane": "live_rearm_941777",
                        "asset_class": "fx",
                    },
                ]
            },
            packet_board={
                "rows": [
                    {
                        "packet_id": "gbpusd_adaptive_comparison_packet",
                        "lane_name": "shadow_gbpusd_m15_trend_harvest_v1",
                        "action_status": "hold_launch_packet_defined_not_started",
                        "action_read": "adaptive GBP packet is defined but intentionally held until the first deliberate shadow launch",
                        "execution_watchdog_status": "",
                        "command": ["python", "scripts/live_penetration_lattice.py", "--max-open-per-side", "4", "--max-floating-loss-usd", "20"],
                        "authority_inputs": ["reports/adaptive_lab_queue.json", "reports/adaptive_incumbent_study_board.json"],
                    },
                    {
                        "packet_id": "usdjpy_bounded_forward_proof",
                        "lane_name": "shadow_usdjpy_gap2",
                        "action_status": "launch_now_manual_packet",
                        "action_read": "bounded proof relaunch packet is now explicit and ready for manual relaunch",
                        "execution_watchdog_status": "paused",
                        "command": ["python", "scripts/live_penetration_lattice.py"],
                        "authority_inputs": ["reports/adaptive_lab_queue.json", "reports/adaptive_lattice_proof_board.json"],
                    },
                ]
            },
            shared_score_board={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "comparison_verdict": "incumbent_still_leading",
                        "score_gap": -11,
                        "incumbent": {"lane": "live_btcusd_m15_warp_941781", "score_total": 7},
                        "adaptive": {
                            "lane": "shadow_btcusd_m15_warp_restore_v1",
                            "score_total": -4,
                            "realized_usd": -17.56,
                            "close_count": 1,
                            "usd_per_close": -17.56,
                            "first_path_verdict": "never_green_toxic_continuation",
                            "unified_objective_verdict": "toxic_path_untradeable",
                        },
                    },
                    {
                        "symbol": "GBPUSD",
                        "comparison_verdict": "incumbent_still_leading",
                        "score_gap": -3,
                        "incumbent": {"lane": "live_rearm_941777", "score_total": 9},
                        "adaptive": {
                            "lane": "shadow_gbpusd_m15_trend_harvest_v1",
                            "score_total": 6,
                            "realized_usd": 0.04,
                            "close_count": 1,
                            "usd_per_close": 0.04,
                            "first_path_verdict": "green_and_monetized",
                            "unified_objective_verdict": "flat_or_insufficient_sample",
                        },
                    },
                    {
                        "symbol": "USDJPY",
                        "comparison_verdict": "no_incumbent_score",
                        "score_gap": None,
                        "incumbent": {},
                        "adaptive": {"lane": "shadow_usdjpy_gap2", "score_total": None},
                    },
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["verdict_counts"]["rejected"], 1)
        self.assertEqual(summary["verdict_counts"]["research_only"], 2)
        self.assertEqual(summary["verdict_counts"]["shadow_ready"], 2)
        self.assertEqual(summary["top_non_rejected_candidate_id"], "btc_restore_comparison_shadow")
        self.assertEqual(summary["btc_max_profit_verdict"], "adaptive_candidate_defined_but_unproven")
        self.assertEqual(summary["early_green_monetization_pass_count"], 0)
        self.assertEqual(summary["early_green_monetization_fail_count"], 3)
        self.assertEqual(summary["live_slot_superiority_pass_count"], 0)
        self.assertEqual(summary["live_slot_superiority_fail_count"], 3)

        indexed = {row["candidate_id"]: row for row in payload["candidates"]}
        self.assertEqual(indexed["btc_restore_comparison_shadow"]["verdict"], "shadow_ready")
        self.assertEqual(indexed["btc_parked_artifact_review"]["verdict"], "rejected")
        self.assertEqual(indexed["btc_true_adaptive_candidate"]["verdict"], "research_only")
        self.assertEqual(indexed["gbpusd_adaptive_comparison_packet"]["verdict"], "shadow_ready")
        self.assertEqual(indexed["usdjpy_bounded_forward_proof"]["verdict"], "research_only")
        restore_checks = {row["check_id"]: row for row in indexed["btc_restore_comparison_shadow"]["checks"]}
        self.assertEqual(restore_checks["early_green_monetization"]["status"], "fail")
        self.assertIn("realized_usd=-17.56", restore_checks["early_green_monetization"]["evidence"])
        self.assertEqual(indexed["btc_restore_comparison_shadow"]["runtime_obligation_class"], "prove_guarded_open_admission_with_cluster_escape")
        self.assertEqual(restore_checks["runtime_safety"]["status"], "pass")
        self.assertEqual(restore_checks["live_slot_superiority"]["status"], "fail")
        self.assertIn("comparison_verdict=incumbent_still_leading", restore_checks["live_slot_superiority"]["evidence"][0])
        self.assertIn("runtime_obligation_class=prove_guarded_open_admission_with_cluster_escape", restore_checks["runtime_safety"]["evidence"])
        self.assertIn("guarded-toxic-flow runtime contract", indexed["btc_restore_comparison_shadow"]["candidate_read"])
        adaptive_btc_checks = {row["check_id"]: row for row in indexed["btc_true_adaptive_candidate"]["checks"]}
        self.assertEqual(adaptive_btc_checks["early_green_monetization"]["status"], "fail")
        self.assertIn("adaptive_pre_start_carry_realized_usd=-17.77", adaptive_btc_checks["early_green_monetization"]["evidence"])
        gbp_checks = {row["check_id"]: row for row in indexed["gbpusd_adaptive_comparison_packet"]["checks"]}
        self.assertEqual(gbp_checks["early_green_monetization"]["status"], "warn")
        self.assertEqual(gbp_checks["launch_packet_clarity"]["status"], "pass")
        self.assertEqual(gbp_checks["runtime_safety"]["status"], "pass")
        self.assertEqual(gbp_checks["live_slot_superiority"]["status"], "fail")
        usdjpy_checks = {row["check_id"]: row for row in indexed["usdjpy_bounded_forward_proof"]["checks"]}
        self.assertEqual(usdjpy_checks["runtime_safety"]["status"], "pass")
        self.assertEqual(usdjpy_checks["launch_packet_clarity"]["status"], "pass")
        self.assertEqual(usdjpy_checks["live_slot_superiority"]["status"], "warn")
        self.assertIn("relaunch packet is now explicit", indexed["usdjpy_bounded_forward_proof"]["candidate_read"])
        self.assertIn("btc_guarded_toxic_flow_overlay_proof", [row["action_id"] for row in payload["next_actions"]])

    def test_render_markdown_mentions_candidate_verdicts(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T05:34:05Z",
                "summary": {
                    "candidate_count": 1,
                    "verdict_counts": {"rejected": 0, "research_only": 1, "shadow_ready": 0, "promotion_ready": 0},
                    "top_non_rejected_candidate_id": "usdjpy_bounded_forward_proof",
                    "btc_recommended_branch_id": "launch_restore_comparison_shadow",
                    "btc_max_profit_verdict": "adaptive_candidate_defined_but_unproven",
                },
                "leadership_read": ["one"],
                "candidates": [
                    {
                        "candidate_id": "usdjpy_bounded_forward_proof",
                        "title": "Run fresh bounded proof",
                        "symbol": "USDJPY",
                        "verdict": "research_only",
                        "queue_status": "ready",
                        "lane": "runtime proof",
                        "candidate_read": "Research only.",
                        "queue_why": "why",
                        "fail_count": 0,
                        "warn_count": 2,
                        "failing_checks": [],
                        "warning_checks": ["launch_packet_clarity"],
                        "supporting_evidence": ["reports/adaptive_lab_queue.json"],
                        "checks": [{"check_id": "branch_clarity", "status": "pass", "read": "explicit branch"}],
                    }
                ],
                "next_actions": [
                    {"action_id": "btc_cash_harvest_forward_proof", "source": "reports/adaptive_incumbent_study_board.json", "read": "btc contract read"},
                    {"action_id": "usdjpy_bounded_launch_packet", "source": "docs/adaptive_harness_acceptance_checklist.md", "read": "Create packet."},
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive Harness Acceptance Verdict Board", markdown)
        self.assertIn("usdjpy_bounded_forward_proof", markdown)
        self.assertIn("research_only", markdown)
        self.assertIn("usdjpy_bounded_launch_packet", markdown)
        self.assertIn("btc_cash_harvest_forward_proof", markdown)


if __name__ == "__main__":
    unittest.main()
