from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_lattice_perfection_scorecard_board as board


class BuildAdaptiveLatticePerfectionScorecardBoardTests(unittest.TestCase):
    def test_build_payload_scores_current_shape_as_instrumented_but_not_yet_perfect(self) -> None:
        payload = board.build_payload(
            formula_coverage={
                "summary": {
                    "verdict_counts": {"true_range_atr_ready": 2, "atr_ready": 3},
                    "formula_input_debt_symbols": [],
                }
            },
            proof_board={
                "generated_at": "2026-04-16T05:00:00Z",
                "blockers": [{"blocker_id": "bounded_close_style_runtime_fault", "active": False}],
                "rows": [
                    {"symbol": "BTCUSD", "stage": "shadow_ready", "status": "ok"},
                    {"symbol": "ETHUSD", "stage": "probation", "status": "ok"},
                ],
            },
            adaptive_queue={
                "ready_count": 1,
                "decision_gated_count": 1,
                "tasks": [
                    {
                        "task_id": "btc_restore_comparison_shadow",
                        "runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                        "runtime_obligation_read": "guard opens and collapse burst risk",
                    }
                ],
                "summary": {
                    "highest_priority_ready_task_id": "btc_adaptive_posture_reconciliation",
                    "highest_priority_ready_title": "Reconcile BTC adaptive posture before any new adaptive relaunch",
                    "runtime_obligation_task_count": 1,
                    "highest_priority_runtime_obligation_task_id": "btc_restore_comparison_shadow",
                    "highest_priority_runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                },
            },
            runtime_audit={
                "status": "runtime_present_manual_review_required",
                "summary": {
                    "completion_read": "BTC adaptive work still needs posture reconciliation before any new relaunch."
                },
                "runtime_lane": {
                    "runner_session_trade_closes": 0,
                    "runner_session_trade_realized_usd": 0.0,
                    "pre_start_state_carry_realized_usd": -17.77,
                    "max_open_per_side": 6,
                },
                "checks": [
                    {"check_id": "runtime_direct_live", "status": "warn"},
                    {"check_id": "design_max_open", "status": "pass"},
                ],
            },
            restore_board={
                "restore_candidate": {
                    "lane": "shadow_btcusd_m15_warp_restore_v1",
                    "verdict": "launch_shadow_restore_comparison",
                    "action": "Launch new shadow with optimal geometry for comparison.",
                }
            },
            controller_priors={
                "global_policy": {"graduation_funnel": {"shadow_to_live": "requires forward proof"}},
                "symbol_priors": {"BTCUSD": {"controller_role": "crypto_split_baseline"}},
            },
            incumbent_study={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "btc_max_profit_comparison": {
                            "verdict": "adaptive_candidate_defined_but_unproven",
                            "restore_lane": "shadow_btcusd_m15_warp_restore_v1",
                            "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                            "read": "btc contract read",
                        },
                    }
                ]
            },
        )

        self.assertEqual(payload["summary"]["overall_verdict"], "instrumented_but_not_yet_perfect")
        self.assertEqual(payload["summary"]["total_score"], 8)
        indexed = {row["category_id"]: row for row in payload["categories"]}
        self.assertEqual(indexed["state_reading_honesty"]["verdict"], "strong")
        self.assertEqual(indexed["geometry_close_rearm_coherence"]["verdict"], "mixed")
        self.assertEqual(indexed["early_green_monetization"]["verdict"], "weak")
        self.assertEqual(indexed["telemetry_explainability"]["verdict"], "strong")
        self.assertEqual(payload["summary"]["restore_candidate_lane"], "shadow_btcusd_m15_warp_restore_v1")
        self.assertEqual(payload["summary"]["btc_max_profit_verdict"], "adaptive_candidate_defined_but_unproven")
        self.assertEqual(payload["summary"]["highest_priority_runtime_obligation_task_id"], "btc_restore_comparison_shadow")
        self.assertEqual(payload["summary"]["highest_priority_runtime_obligation_class"], "prove_guarded_open_admission_with_cluster_escape")
        self.assertIn("runtime_overlay_obligation", [row["action_id"] for row in payload["next_actions"]])

    def test_build_payload_marks_formula_debt_and_missing_governance_as_weak(self) -> None:
        payload = board.build_payload(
            formula_coverage={
                "summary": {
                    "verdict_counts": {"fallback_only_current_atr_step_coeff": 1},
                    "formula_input_debt_symbols": ["BTCUSD"],
                }
            },
            proof_board={"blockers": [{"blocker_id": "bounded_close_style_runtime_fault", "active": True}], "rows": []},
            adaptive_queue={"ready_count": 0, "decision_gated_count": 0, "summary": {}},
            runtime_audit={"runtime_lane": {}, "checks": []},
            restore_board={"restore_candidate": {}},
            controller_priors={},
            incumbent_study={"rows": []},
        )

        indexed = {row["category_id"]: row for row in payload["categories"]}
        self.assertEqual(indexed["state_reading_honesty"]["verdict"], "weak")
        self.assertEqual(indexed["geometry_close_rearm_coherence"]["verdict"], "weak")
        self.assertEqual(indexed["portfolio_governance"]["verdict"], "mixed")

    def test_render_markdown_mentions_score_and_next_actions(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T05:00:00Z",
                "summary": {"overall_verdict": "instrumented_but_not_yet_perfect", "total_score": 7, "max_score": 14},
                "leadership_read": ["one"],
                "categories": [
                    {
                        "category_id": "state_reading_honesty",
                        "title": "State-Reading Honesty",
                        "verdict": "strong",
                        "score": 2,
                        "max_score": 2,
                        "rationale": "rationale",
                        "evidence": ["e1"],
                    }
                ],
                "next_actions": [
                    {"action_id": "queue_ready_posture_reconciliation", "source": "adaptive_lab_queue", "read": "Reconcile posture"},
                    {"action_id": "btc_max_profit_contract", "source": "adaptive_incumbent_study_board", "read": "btc contract"},
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive Lattice Perfection Scorecard Board", markdown)
        self.assertIn("instrumented_but_not_yet_perfect", markdown)
        self.assertIn("queue_ready_posture_reconciliation", markdown)
        self.assertIn("btc_max_profit_contract", markdown)


if __name__ == "__main__":
    unittest.main()
