#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_lab_queue as queue_mod


class AdaptiveLabQueueTests(unittest.TestCase):
    def test_next_action_class_pair_preserves_detailed_vs_seat_compat(self) -> None:
        seat_class, detailed_class = queue_mod.next_action_class_pair("micro_harvest", "")
        self.assertEqual(detailed_class, "validate_microstructure_capture_under_real_friction")
        self.assertEqual(seat_class, "shadow_compare_and_score")

        seat_class, detailed_class = queue_mod.next_action_class_pair("cash_repair_harvest", "adaptive_shape_defined_packet_missing")
        self.assertEqual(detailed_class, "prove_close_conversion_before_extension")
        self.assertEqual(seat_class, "prove_executability_and_survival_before_promotion")

        seat_class, detailed_class = queue_mod.next_action_class_pair("trend_harvest", "adaptive_shape_defined_packet_missing")
        self.assertEqual(detailed_class, "build_executable_comparison_packet")
        self.assertEqual(seat_class, "build_executable_comparison_packet")

    def test_queue_reflects_explicit_btc_branch_split_and_profit_mode_followups(self) -> None:
        payload = queue_mod.build_payload(refresh_inputs=False)
        rows = {row["task_id"]: row for row in payload["tasks"]}
        summary = payload["summary"]

        self.assertEqual(rows["btc_restore_comparison_shadow"]["status"], "ready")
        self.assertIn("restore comparison", rows["btc_restore_comparison_shadow"]["title"].lower())
        self.assertEqual(rows["btc_restore_comparison_shadow"]["profit_mode"], "guarded_toxic_flow")
        self.assertEqual(
            rows["btc_restore_comparison_shadow"]["runtime_obligation_class"],
            "prove_guarded_open_admission_with_cluster_escape",
        )
        self.assertIn("cluster_aware_escape", rows["btc_restore_comparison_shadow"]["runtime_overlays"])
        self.assertEqual(rows["btc_true_adaptive_candidate"]["status"], "blocked")
        self.assertEqual(rows["btc_parked_artifact_review"]["status"], "completed")
        self.assertEqual(rows["gbpusd_adaptive_comparison_packet"]["status"], "ready")
        self.assertEqual(rows["gbpusd_adaptive_comparison_packet"]["profit_mode"], "trend_harvest")
        self.assertEqual(rows["usdcad_first_live_seat_contract"]["status"], "ready")
        self.assertEqual(rows["usdcad_first_live_seat_contract"]["lane"], "shadow HH")
        self.assertEqual(rows["usdcad_first_live_seat_contract"]["next_action_class"], "formalize_first_live_seat_contract")
        self.assertEqual(rows["usdjpy_bounded_forward_proof"]["status"], "ready")
        self.assertEqual(rows["usdjpy_bounded_forward_proof"]["next_action_class"], "shadow_compare_and_score")
        self.assertEqual(rows["eurusd_friction_survivor_research"]["status"], "blocked")
        self.assertEqual(rows["usdjpy_bounded_fault_repair"]["status"], "completed")
        self.assertGreaterEqual(payload["ready_count"], 4)
        self.assertGreaterEqual(payload["completed_count"], 5)
        self.assertEqual(summary["highest_priority_ready_task_id"], "btc_restore_comparison_shadow")
        self.assertEqual(summary["highest_priority_blocked_task_id"], "btc_true_adaptive_candidate")
        self.assertEqual(summary["highest_priority_runtime_obligation_task_id"], "btc_restore_comparison_shadow")
        self.assertEqual(summary["highest_priority_runtime_obligation_class"], "prove_guarded_open_admission_with_cluster_escape")
        self.assertEqual(summary["btc_recommended_branch_id"], "launch_restore_comparison_shadow")
        self.assertEqual(summary["btc_doctrine_target_branch_id"], "define_true_adaptive_candidate_then_build")
        self.assertEqual(len(payload["input_surfaces"]), 12)
        input_surfaces = {row["surface_id"]: row for row in payload["input_surfaces"]}
        self.assertTrue(input_surfaces["gbpusd_adaptive_shadow_packet"]["path"].endswith("gbpusd_adaptive_shadow_packet.json"))
        self.assertTrue(input_surfaces["per_symbol_live_seat_board"]["path"].endswith("per_symbol_live_seat_board.json"))
        self.assertTrue(input_surfaces["gbpusd_adaptive_shadow_packet"]["status"])
        self.assertTrue(payload["leadership_read"])

    def test_gbp_queue_inherits_running_runtime_truth_from_study(self) -> None:
        def fake_load_json(path: Path) -> dict:
            if path == queue_mod.PROOF_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "rows": [
                        {"symbol": "BTCUSD", "recommended_shape_id": "btcusd_rangeatr_cash_harvest_v1", "profit_mode": "guarded_toxic_flow", "blockers": []},
                        {"symbol": "EURUSD", "recommended_shape_id": "eurusd_mixed_floor_v1", "profit_mode": "friction_survivor", "blockers": []},
                        {"symbol": "GBPUSD", "recommended_shape_id": "gbpusd_trend_harvest_v1", "profit_mode": "trend_harvest", "blockers": []},
                        {"symbol": "NZDUSD", "recommended_shape_id": "nzdusd_asym_probe_v1", "profit_mode": "trend_harvest", "blockers": []},
                        {"symbol": "USDJPY", "recommended_shape_id": "usdjpy_bounded_survival_v1", "profit_mode": "friction_survivor", "blockers": []},
                    ],
                }
            if path == queue_mod.TRANSFER_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "rows": [
                        {"symbol": "GBPUSD", "recommended_shape_id": "gbpusd_trend_harvest_v1", "rationale": "gbp transfer"},
                        {"symbol": "NZDUSD", "recommended_shape_id": "nzdusd_asym_probe_v1", "rationale": "nzd transfer"},
                    ],
                }
            if path == queue_mod.OPTIMIZER_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "rows": [
                        {"surface_id": "allocation_optimizer"},
                        {"surface_id": "optimal_portfolio_optimizer"},
                        {"surface_id": "atr_step_optimizer"},
                    ],
                }
            if path == queue_mod.OPTIMIZER_RECON_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "rows": [
                        {"surface_id": "allocation_optimizer"},
                        {"surface_id": "optimal_portfolio_optimizer"},
                    ],
                }
            raise AssertionError(f"unexpected load_json path: {path}")

        def fake_load_optional_json(path: Path) -> dict | None:
            if path == queue_mod.OPTIMIZER_DECISION_PATH:
                return {
                    "summary": {"decision_ready_surfaces": 2},
                    "rows": [{"surface_id": "optimal_portfolio_optimizer", "decision": "canonical-only"}],
                }
            if path == queue_mod.CONTROLLER_PRIORS_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "symbol_priors": {
                        "BTCUSD": {"promotion_action": "hold_until_buy_realign"},
                    },
                }
            if path == queue_mod.NZDUSD_PROBE_PATH:
                return None
            if path == queue_mod.GBPUSD_PACKET_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "summary": {
                        "packet_defined": True,
                        "completion_read": "GBP packet is explicit and dedicated.",
                    }
                }
            if path == queue_mod.BTC_BRANCH_DECISION_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "summary": {
                        "recommended_branch_id": "launch_restore_comparison_shadow",
                        "doctrine_target_branch_id": "define_true_adaptive_candidate_then_build",
                    },
                    "rows": [
                        {
                            "branch_id": "launch_restore_comparison_shadow",
                            "launch_status": "already_running_monitor_only",
                            "title": "Launch the BTC M15 warp restore comparison shadow",
                            "why": "btc restore why",
                            "allowed_inputs": ["shadow_btcusd_m15_warp_restore_v1"],
                        },
                        {
                            "branch_id": "define_true_adaptive_candidate_then_build",
                            "title": "Define and build the true downtrend-aware adaptive BTC candidate",
                            "why": "btc true why",
                            "allowed_inputs": ["btcusd_rangeatr_cash_harvest_v1"],
                            "blockers": ["restore_comparison_shadow_should_land_first"],
                        },
                        {
                            "branch_id": "hold_parked_artifact_only",
                            "title": "Keep the parked BTC adaptive artifact in hold/manual-review only",
                            "why": "btc parked why",
                            "allowed_inputs": ["shadow_btcusd_m15_adaptive_regime"],
                        },
                    ],
                }
            if path == queue_mod.INCUMBENT_STUDY_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "rows": [
                        {
                            "symbol": "BTCUSD",
                            "adaptive_profit_mode": "guarded_toxic_flow",
                            "study_status": "study_ready",
                            "adaptive_runtime_overlay_read": "",
                            "adaptive_runtime_overlays": [],
                        },
                        {
                            "symbol": "GBPUSD",
                            "adaptive_shape_id": "gbpusd_trend_harvest_v1",
                            "adaptive_profit_mode": "trend_harvest",
                            "study_status": "first_path_opened_wait_shared_score_refresh",
                            "adaptive_runtime_status": "already_running_monitor_only",
                            "incumbent_lane": "live_rearm_941777",
                            "adaptive_runtime_overlay_read": "",
                            "adaptive_runtime_overlays": [],
                        },
                        {
                            "symbol": "EURUSD",
                            "adaptive_profit_mode": "friction_survivor",
                            "study_status": "research_only_adaptive_candidate",
                            "adaptive_runtime_overlay_read": "",
                            "adaptive_runtime_overlays": [],
                        },
                        {
                            "symbol": "USDJPY",
                            "adaptive_profit_mode": "friction_survivor",
                            "study_status": "adaptive_candidate_without_incumbent",
                            "adaptive_runtime_overlay_read": "",
                            "adaptive_runtime_overlays": [],
                        },
                        {
                            "symbol": "NZDUSD",
                            "adaptive_profit_mode": "trend_harvest",
                            "study_status": "research_only_adaptive_candidate",
                            "adaptive_runtime_overlay_read": "",
                            "adaptive_runtime_overlays": [],
                        },
                    ],
                }
            if path == queue_mod.SEAT_BOARD_PATH:
                return {
                    "generated_at": "2026-04-16T19:00:00+00:00",
                    "rows": [
                        {
                            "symbol": "USDCAD",
                            "seat_verdict": "no_live_seat",
                            "best_challenger_lane": "penetration_lattice_shadow_usdcad_m15_hh_breakout_v1",
                            "best_challenger_candidate_class": "ready_for_shadow_discussion",
                            "best_challenger_runtime_status": "forward_proof_started",
                            "seat_unblocker_action": "prepare_first_live_seat_case",
                            "seat_unblocker_read": "The symbol has no live incumbent and the challenger now has comparable proof, so the next move is to turn that proof into a first live-seat case.",
                            "seat_actionability_status": "local_actionable_unqueued",
                            "seat_actionability_read": "The next honest move is to formalize the first live-seat decision contract so this running proof seam becomes queue-backed.",
                            "seat_execution_gate_status": "actionable_but_missing_queue_contract",
                        }
                    ],
                }
            return {}

        with patch.object(queue_mod, "load_json", side_effect=fake_load_json), patch.object(
            queue_mod, "load_optional_json", side_effect=fake_load_optional_json
        ):
            rows = {row["task_id"]: row for row in queue_mod.build_tasks()}

        gbp = rows["gbpusd_adaptive_comparison_packet"]
        usdcad = rows["usdcad_first_live_seat_contract"]
        self.assertEqual(gbp["machine_truth"]["adaptive_runtime_status"], "already_running_monitor_only")
        self.assertIn("already running", gbp["why"])
        self.assertIn("first-close collection", gbp["why"])
        self.assertEqual(usdcad["status"], "ready")
        self.assertEqual(usdcad["next_action_class"], "formalize_first_live_seat_contract")
        self.assertIn("first live-seat case", usdcad["why"])


if __name__ == "__main__":
    unittest.main()
