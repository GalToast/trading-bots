#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_per_symbol_live_seat_board as board


class BuildPerSymbolLiveSeatBoardTests(unittest.TestCase):
    def test_registry_lane_symbols_parses_single_and_multi_symbol_args(self) -> None:
        payload = {
            "lanes": [
                {
                    "name": "live_fx",
                    "restart_args": ["script.py", "--symbols", "EURUSD", "GBPUSD", "--poll-seconds", "1"],
                },
                {
                    "name": "live_btc",
                    "restart_args": ["script.py", "--symbol", "BTCUSD", "--timeframe", "M15"],
                },
            ]
        }

        self.assertEqual(
            board.registry_lane_symbols(payload),
            {
                "live_fx": ["EURUSD", "GBPUSD"],
                "live_btc": ["BTCUSD"],
            },
        )

    def test_build_live_holder_rows_prefers_live_dashboard_cashflow_over_zero_booked_board(self) -> None:
        live_payload = {
            "rows": [
                {
                    "lane": "live_eurusd_adaptive_harness_941885",
                    "enabled": True,
                    "kind": "live_fx",
                    "status": "ok",
                    "evidence_basis": "carry_weighted_live",
                    "operator_posture": "require_fresh_forward_sample",
                    "broker_net_usd": 18.09,
                    "fresh_session_booked_usd": 15.86,
                    "fresh_session_usd_per_hour": 7.93,
                    "close_count": 14,
                    "broker_open_count": 8,
                    "notes": "runner_session_since_start=10c/+15.86 6o, pre_start_state_carry=4c/+2.23",
                }
            ]
        }
        registry_payload = {
            "lanes": [
                {
                    "name": "live_eurusd_adaptive_harness_941885",
                    "restart_args": ["script.py", "--symbol", "EURUSD"],
                }
            ]
        }
        booked_payload = {
            "live": {
                "rows": [
                    {"lane": "live_eurusd_adaptive_harness_941885", "booked_usd": 0.0},
                ]
            }
        }

        rows = board.build_live_holder_rows(live_payload, registry_payload, booked_payload)

        self.assertEqual(rows["EURUSD"][0]["booked_usd"], 18.09)
        self.assertEqual(rows["EURUSD"][0]["fresh_session_booked_usd"], 15.86)
        self.assertEqual(rows["EURUSD"][0]["fresh_session_usd_per_hour"], 7.93)

    def test_live_holder_objective_proxy_rewards_fresh_velocity_and_penalizes_flat_quarantine(self) -> None:
        eur_score = board.live_holder_objective_proxy(
            "EURUSD",
            {
                "status": "ok",
                "evidence_basis": "carry_weighted_live",
                "operator_posture": "require_fresh_forward_sample",
                "booked_usd": 27.68,
                "fresh_session_booked_usd": 25.55,
                "fresh_session_usd_per_hour": 11.31,
                "close_count": 20,
                "open_count": 4,
            },
            seat_conflict=True,
            btc_concentration_payload={"summary": {}},
        )
        btc_score = board.live_holder_objective_proxy(
            "BTCUSD",
            {
                "status": "quarantined",
                "evidence_basis": "carry_weighted_live",
                "operator_posture": "repair_runtime_first",
                "booked_usd": 1456.94,
                "fresh_session_booked_usd": 0.0,
                "fresh_session_usd_per_hour": 0.0,
                "close_count": 288,
                "open_count": 0,
            },
            seat_conflict=False,
            btc_concentration_payload={"summary": {}},
        )

        self.assertGreater(eur_score["score"], btc_score["score"])
        self.assertGreater(eur_score["components"]["fresh_velocity_component"], 0.0)
        self.assertEqual(btc_score["components"]["runtime_penalty"], 4.0)
        self.assertEqual(btc_score["components"]["flat_nonmonetizing_penalty"], 3.0)

    def test_build_payload_marks_btc_as_live_demotion_candidate(self) -> None:
        live_payload = {
            "rows": [
                {
                    "lane": "live_btcusd_m15_warp_941781",
                    "enabled": True,
                    "kind": "live_crypto",
                    "status": "ok",
                    "evidence_basis": "carry_weighted_live",
                    "operator_posture": "require_fresh_forward_sample",
                    "broker_net_usd": 1248.75,
                    "close_count": 277,
                    "broker_open_count": 0,
                    "notes": "pre_start_state_carry=277c/+1248.75",
                },
                {
                    "lane": "live_btcusd_exc2_tight_941779",
                    "enabled": True,
                    "kind": "live_crypto",
                    "status": "ok",
                    "evidence_basis": "carry_weighted_live",
                    "operator_posture": "require_fresh_forward_sample",
                    "broker_net_usd": 394.85,
                    "close_count": 108,
                    "broker_open_count": 0,
                    "notes": "pre_start_state_carry=46c/+1643.67",
                },
                {
                    "lane": "live_rearm_941777",
                    "enabled": True,
                    "kind": "live_fx",
                    "status": "ok",
                    "evidence_basis": "graduated_live_reference",
                    "operator_posture": "keep_live_reference",
                    "broker_net_usd": 724.43,
                    "close_count": 320,
                    "broker_open_count": 4,
                    "notes": "fx_grad=live progress=graduated(100.0%)",
                },
                {
                    "lane": "live_momentum_alpha50_941778",
                    "enabled": True,
                    "kind": "live_fx",
                    "status": "ok",
                    "evidence_basis": "carry_weighted_live",
                    "operator_posture": "require_fresh_forward_sample",
                    "broker_net_usd": 24.91,
                    "close_count": 188,
                    "broker_open_count": 7,
                    "notes": "pre_start_state_carry=37c/-88.14",
                },
            ]
        }
        registry_payload = {
            "lanes": [
                {
                    "name": "live_btcusd_m15_warp_941781",
                    "restart_args": ["script.py", "--symbol", "BTCUSD"],
                },
                {
                    "name": "live_btcusd_exc2_tight_941779",
                    "restart_args": ["script.py", "--symbol", "BTCUSD"],
                },
                {
                    "name": "live_rearm_941777",
                    "restart_args": ["script.py", "--symbols", "EURUSD", "GBPUSD"],
                },
                {
                    "name": "live_momentum_alpha50_941778",
                    "restart_args": ["script.py", "--symbols", "EURUSD", "GBPUSD", "NZDUSD"],
                },
            ]
        }
        fx_payload = {
            "watch_lead": {"candidate": "symbol-specific close-policy map + session gate"},
            "rows": [
                {
                    "lane_name": "shadow_fx_close_policy_mixed_session_gated",
                    "candidate": "symbol-specific close-policy map + session gate",
                    "scope": "EURUSD + GBPUSD",
                    "readiness": "shadow_collecting",
                    "gate_status": "waiting_good_session_window",
                    "recommendation": "wait for the next good session",
                    "evidence": "80 closes, $+27.41 realized, 9 open",
                    "operator_posture": "running; session_gate=on; gated_now=no; 9 open; closes=80",
                }
            ],
        }
        btc_concentration_payload = {
            "summary": {
                "operator_posture": "carry_until_threshold_break",
            }
        }
        adaptive_acceptance_payload = {
            "candidates": [
                {"candidate_id": "btc_restore_comparison_shadow", "symbol": "BTCUSD", "verdict": "shadow_ready"},
                {"candidate_id": "usdjpy_bounded_proof_refresh", "symbol": "USDJPY", "verdict": "research_only"},
            ]
        }
        adaptive_overnight_payload = {
            "rows": [
                {
                    "packet_id": "btc_restore_comparison_shadow",
                    "lane_name": "shadow_btcusd_m15_warp_restore_v1",
                    "action_status": "hold_runtime_repair_candidate",
                    "action_read": "recommended BTC control branch is paused pending runtime repair",
                    "why": "failed under supervision tonight",
                    "artifact_trade_closes": 13,
                    "artifact_open_count": 0,
                    "first_path_close_realized_pnl": -17.56,
                },
                {
                    "packet_id": "nzdusd_transfer_probe",
                    "lane_name": "shadow_nzdusd_m15_asym",
                    "action_status": "already_running_monitor_only",
                    "action_read": "shadow lane is already running under research-only posture",
                    "why": "research-only transfer probe",
                },
                {
                    "packet_id": "shadow_usdjpy_gap2",
                    "lane_name": "shadow_usdjpy_gap2",
                    "action_status": "hold_disabled_proof_candidate",
                    "action_read": "bounded proof remains a candidate",
                    "why": "proof pending",
                },
            ]
        }
        hungry_hippo_payload = {
            "rows": [
                {
                    "symbol": "USDCAD",
                    "generalization_status": "ready_for_shadow_discussion",
                    "runtime_state": "forward_proof_started",
                    "next_action": "wait for first close",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                    "guardrail_status": "promotable_now",
                    "realized_closes": 9,
                    "realized_net_usd": 7.65,
                    "current_open_count": 11,
                    "state_path": "reports/penetration_lattice_shadow_usdcad_m15_hh_breakout_v1_state.json",
                }
            ]
        }
        booked_pnl_payload = {
            "live": {
                "rows": [
                    {"lane": "live_btcusd_m15_warp_941781", "booked_usd": 1248.75},
                    {"lane": "live_btcusd_exc2_tight_941779", "booked_usd": 394.85},
                    {"lane": "live_rearm_941777", "booked_usd": 724.43},
                    {"lane": "live_momentum_alpha50_941778", "booked_usd": 24.91},
                ]
            }
        }
        telemetry_payload = {
            "lanes": [
                {
                    "lane_name": "shadow_nzdusd_m15_asym",
                    "total_closes": 208,
                    "active_closes": 0,
                    "enrichment_score": 90,
                    "enrichment_verdict": "needs_enrichment",
                }
            ]
        }
        adaptive_proof_payload = {
            "rows": [
                {
                    "symbol": "BTCUSD",
                    "stage": "shadow_ready",
                    "profit_mode": "guarded_toxic_flow",
                    "profit_mode_read": "guarded until one-way flow normalizes",
                    "runtime_overlays": ["guard_open_admission", "cluster_aware_escape"],
                    "runtime_overlay_read": "guard opens and escape clustered burst fills as one risk unit",
                },
                {
                    "symbol": "EURUSD",
                    "stage": "research_only",
                    "profit_mode": "friction_survivor",
                    "profit_mode_read": "survivable harvest first",
                },
                {
                    "symbol": "NZDUSD",
                    "stage": "research_only",
                    "profit_mode": "trend_harvest",
                    "profit_mode_read": "let asymmetry do the monetization work",
                },
            ]
        }
        adaptive_lab_queue_payload = {
            "tasks": [
                {
                    "task_id": "btc_restore_comparison_shadow",
                    "priority": 1,
                    "status": "ready",
                    "lane": "shadow crypto",
                    "title": "Launch the BTC M15 warp restore comparison shadow",
                    "allowed_inputs": ["shadow_btcusd_m15_warp_restore_v1"],
                    "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                },
                {
                    "task_id": "btc_true_adaptive_candidate",
                    "priority": 2,
                    "status": "blocked",
                    "lane": "shadow crypto",
                    "title": "Define and build the true downtrend-aware adaptive BTC candidate",
                    "allowed_inputs": ["btcusd_rangeatr_cash_harvest_v1"],
                    "next_action_class": "control_shadow_and_collect_path_safety_evidence",
                },
                {
                    "task_id": "gbpusd_adaptive_comparison_packet",
                    "priority": 4,
                    "status": "ready",
                    "lane": "shadow FX",
                    "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
                    "allowed_inputs": ["gbpusd_trend_harvest_v1", "live_rearm_941777"],
                    "next_action_class": "shadow_compare_and_score",
                },
                {
                    "task_id": "usdjpy_bounded_forward_proof",
                    "priority": 6,
                    "status": "ready",
                    "lane": "shadow FX",
                    "title": "Run fresh USDJPY bounded forward proof under the restored friction-survivor branch",
                    "allowed_inputs": ["usdjpy_bounded_survival_v1"],
                    "next_action_class": "prove_executability_and_survival_before_promotion",
                },
                {
                    "task_id": "eurusd_friction_survivor_research",
                    "priority": 10,
                    "status": "blocked",
                    "lane": "shadow FX",
                    "title": "Keep EURUSD on friction-survivor research until forward proof beats the incumbent",
                    "allowed_inputs": ["eurusd_mixed_floor_v1", "live_rearm_941777"],
                    "next_action_class": "prove_executability_and_survival_before_promotion",
                },
                {
                    "task_id": "nzdusd_transfer_probe",
                    "priority": 5,
                    "status": "completed",
                    "lane": "shadow FX",
                    "title": "Launch NZDUSD adapt-first transfer probe from the GBPUSD donor family",
                    "allowed_inputs": ["nzdusd_asym_probe_v1"],
                    "next_action_class": "keep_in_research_until_forward_proof",
                },
            ]
        }
        adaptive_runner_plan_payload = {
            "symbol": "BTCUSD",
            "status": "ready",
            "runtime_overlay_contract": {
                "supported_overlays": [
                    "guard_open_admission",
                    "cluster_aware_escape",
                    "suppress_additional_levels_after_burst",
                ],
                "requested_overlays": [],
                "executable_overlays": [],
                "unsupported_overlays": [],
                "read": "Controller did not request any runtime overlays for this scaffold. This scaffold can currently express guard_open_admission, cluster_aware_escape, suppress_additional_levels_after_burst when a future controller state requests them.",
            },
        }

        payload = board.build_payload(
            live_payload,
            registry_payload,
            fx_payload,
            btc_concentration_payload,
            adaptive_acceptance_payload,
            adaptive_overnight_payload,
            hungry_hippo_payload,
            booked_pnl_payload,
            telemetry_payload,
            adaptive_proof_payload,
            adaptive_lab_queue_payload,
            adaptive_runner_plan_payload,
        )

        rows = {row["symbol"]: row for row in payload["rows"]}

        btc = rows["BTCUSD"]
        self.assertEqual(btc["current_live_holder_lane"], "live_btcusd_m15_warp_941781")
        self.assertEqual(btc["best_challenger_candidate_class"], "shadow_ready")
        self.assertEqual(btc["best_challenger_runtime_status"], "hold_runtime_repair_candidate")
        self.assertEqual(btc["next_action"], "live_demotion_candidate")
        self.assertTrue(btc["seat_conflict"])
        self.assertEqual(btc["max_profit_objective_status"], "carry_dominated_or_unproven")
        self.assertLess(btc["max_profit_objective_proxy"], 0.0)
        self.assertEqual(btc["objective_displacement_status"], "objective_edge_but_not_launchable")
        self.assertEqual(btc["seat_unblocker_action"], "clear_launchability_blocker")
        self.assertEqual(btc["adaptive_profit_mode"], "guarded_toxic_flow")
        self.assertEqual(btc["seat_unblocker_priority_status"], "queue_ready")
        self.assertEqual(btc["seat_unblocker_priority_rank"], 1)
        self.assertEqual(btc["seat_unblocker_queue_task_id"], "btc_restore_comparison_shadow")
        self.assertEqual(btc["seat_queue_alignment_status"], "queue_ready_aligned")
        self.assertEqual(btc["seat_actionability_status"], "queue_ready_actionable")
        self.assertEqual(btc["seat_contract_gap_status"], "queue_backed_actionable")
        self.assertEqual(btc["seat_overlay_contract_status"], "actionable_under_overlay_contract")
        self.assertEqual(btc["adaptive_runtime_overlays"], ["guard_open_admission", "cluster_aware_escape"])
        self.assertEqual(btc["seat_overlay_launch_bridge_status"], "overlay_launch_bridge_supported_but_unrequested")
        self.assertEqual(btc["seat_execution_gate_status"], "blocked_by_overlay_request_alignment")

        eur = rows["EURUSD"]
        self.assertEqual(eur["current_live_holder_lane"], "live_rearm_941777")
        self.assertEqual(eur["seat_verdict"], "defended_but_contested_live_seat")
        self.assertEqual(eur["next_action"], "keep_live_but_under_audit")
        self.assertEqual(eur["best_challenger_lane"], "shadow_fx_close_policy_mixed_session_gated")
        self.assertEqual(eur["max_profit_objective_status"], "profitable_but_contested_reference")
        self.assertGreater(eur["max_profit_objective_proxy"], 0.0)
        self.assertEqual(eur["best_challenger_objective_status"], "challenger_comparable")
        self.assertEqual(eur["objective_comparison_status"], "incumbent_objective_edge")
        self.assertEqual(eur["objective_displacement_status"], "incumbent_still_leads")
        self.assertEqual(eur["seat_unblocker_action"], "keep_incumbent_collect_challenger_proof")
        self.assertEqual(eur["adaptive_profit_mode"], "friction_survivor")
        self.assertEqual(eur["seat_unblocker_priority_status"], "queue_blocked")
        self.assertEqual(eur["seat_unblocker_priority_rank"], 10)
        self.assertEqual(eur["seat_unblocker_queue_task_id"], "eurusd_friction_survivor_research")
        self.assertEqual(eur["seat_queue_alignment_status"], "queue_blocked_aligned")
        self.assertEqual(eur["seat_actionability_status"], "blocked_by_queue_contract")
        self.assertEqual(eur["seat_contract_gap_status"], "queue_contract_blocked")
        self.assertEqual(eur["seat_overlay_contract_status"], "no_overlay_contract")
        self.assertEqual(eur["seat_overlay_launch_bridge_status"], "no_overlay_launch_bridge_needed")
        self.assertEqual(eur["seat_execution_gate_status"], "blocked_by_queue_contract")

        nzd = rows["NZDUSD"]
        self.assertEqual(nzd["current_live_holder_lane"], "live_momentum_alpha50_941778")
        self.assertEqual(nzd["best_challenger_lane"], "shadow_nzdusd_m15_asym")
        self.assertEqual(nzd["next_action"], "keep_live_but_under_audit")
        self.assertEqual(nzd["max_profit_objective_status"], "carry_dominated_or_unproven")
        self.assertEqual(nzd["best_challenger_objective_status"], "challenger_partially_comparable")
        self.assertEqual(nzd["objective_comparison_status"], "partial_objective_comparison")
        self.assertEqual(nzd["best_challenger_proof_integrity_status"], "telemetry_debt_inherited_only")
        self.assertEqual(nzd["objective_displacement_status"], "comparison_incomplete")
        self.assertEqual(nzd["seat_unblocker_action"], "enrich_challenger_telemetry_first")
        self.assertEqual(nzd["seat_unblocker_priority_status"], "unqueued_action")
        self.assertIsNone(nzd["seat_unblocker_priority_rank"])
        self.assertEqual(nzd["seat_queue_alignment_status"], "no_queue_contract")
        self.assertEqual(nzd["seat_actionability_status"], "local_actionable_unqueued")
        self.assertEqual(nzd["seat_contract_gap_status"], "actionable_missing_queue_contract")
        self.assertEqual(nzd["seat_overlay_contract_status"], "no_overlay_contract")
        self.assertEqual(nzd["seat_overlay_launch_bridge_status"], "no_overlay_launch_bridge_needed")
        self.assertEqual(nzd["seat_execution_gate_status"], "actionable_but_missing_queue_contract")

        usdcad = rows["USDCAD"]
        self.assertEqual(usdcad["current_live_holder_lane"], "")
        self.assertEqual(usdcad["best_challenger_candidate_class"], "ready_for_shadow_discussion")
        self.assertEqual(usdcad["best_challenger_runtime_status"], "forward_proof_started")
        self.assertEqual(usdcad["next_action"], "shadow_challenger_needed")
        self.assertEqual(usdcad["max_profit_objective_status"], "missing_live_seat")
        self.assertEqual(usdcad["best_challenger_objective_status"], "challenger_comparable")
        self.assertEqual(usdcad["objective_comparison_status"], "no_live_incumbent")
        self.assertEqual(usdcad["objective_displacement_status"], "no_live_incumbent")
        self.assertEqual(usdcad["seat_unblocker_action"], "prepare_first_live_seat_case")
        self.assertEqual(usdcad["seat_unblocker_priority_status"], "unqueued_action")
        self.assertEqual(usdcad["seat_queue_alignment_status"], "no_queue_contract")
        self.assertEqual(usdcad["seat_actionability_status"], "local_actionable_unqueued")
        self.assertEqual(usdcad["seat_contract_gap_status"], "actionable_missing_queue_contract")
        self.assertEqual(usdcad["seat_overlay_contract_status"], "no_overlay_contract")
        self.assertEqual(usdcad["seat_overlay_launch_bridge_status"], "no_overlay_launch_bridge_needed")
        self.assertEqual(usdcad["seat_execution_gate_status"], "actionable_but_missing_queue_contract")

        gbp = rows["GBPUSD"]
        self.assertEqual(gbp["seat_queue_alignment_status"], "queue_ready_aligned")
        self.assertEqual(gbp["seat_actionability_status"], "queue_ready_actionable")
        self.assertEqual(gbp["seat_contract_gap_status"], "queue_backed_actionable")
        self.assertEqual(gbp["seat_execution_gate_status"], "ready_for_seat_execution")

        usdjpy = rows["USDJPY"]
        self.assertEqual(usdjpy["seat_queue_alignment_status"], "queue_ready_aligned")
        self.assertEqual(usdjpy["seat_actionability_status"], "queue_ready_actionable")
        self.assertEqual(usdjpy["seat_contract_gap_status"], "queue_backed_actionable")
        self.assertEqual(usdjpy["seat_execution_gate_status"], "ready_for_seat_execution")

        self.assertEqual(
            payload["summary"]["seat_unblocker_priority_status_counts"],
            {
                "queue_ready": 3,
                "unqueued_action": 2,
                "queue_blocked": 1,
            },
        )
        self.assertEqual(
            payload["summary"]["seat_unblocker_priority_symbols"],
            ["BTCUSD", "GBPUSD", "USDJPY", "USDCAD", "NZDUSD", "EURUSD"],
        )
        self.assertEqual(payload["summary"]["highest_priority_seat_symbol"], "BTCUSD")
        self.assertEqual(
            payload["summary"]["seat_queue_alignment_counts"],
            {
                "no_queue_contract": 2,
                "queue_blocked_aligned": 1,
                "queue_ready_aligned": 3,
            },
        )
        self.assertEqual(payload["summary"]["queue_precedes_seat_symbols"], [])
        self.assertEqual(
            payload["summary"]["seat_actionability_counts"],
            {
                "queue_ready_actionable": 3,
                "blocked_by_queue_contract": 1,
                "local_actionable_unqueued": 2,
            },
        )
        self.assertEqual(
            payload["summary"]["actionable_seat_symbols"],
            ["BTCUSD", "GBPUSD", "USDJPY", "USDCAD", "NZDUSD"],
        )
        self.assertEqual(payload["summary"]["highest_actionable_seat_symbol"], "BTCUSD")
        self.assertEqual(
            payload["summary"]["seat_contract_gap_counts"],
            {
                "queue_backed_actionable": 3,
                "queue_contract_blocked": 1,
                "actionable_missing_queue_contract": 2,
            },
        )
        self.assertEqual(payload["summary"]["actionable_unqueued_symbols"], ["USDCAD", "NZDUSD"])
        self.assertEqual(payload["summary"]["highest_actionable_queue_backed_symbol"], "BTCUSD")
        self.assertEqual(
            payload["summary"]["seat_overlay_contract_counts"],
            {
                "actionable_under_overlay_contract": 1,
                "no_overlay_contract": 5,
            },
        )
        self.assertEqual(payload["summary"]["overlay_constrained_symbols"], ["BTCUSD"])
        self.assertEqual(payload["summary"]["actionable_overlay_constrained_symbols"], ["BTCUSD"])
        self.assertEqual(
            payload["summary"]["seat_overlay_launch_bridge_counts"],
            {
                "overlay_launch_bridge_supported_but_unrequested": 1,
                "no_overlay_launch_bridge_needed": 5,
            },
        )
        self.assertEqual(payload["summary"]["overlay_launch_gap_symbols"], ["BTCUSD"])
        self.assertEqual(
            payload["summary"]["seat_execution_gate_counts"],
            {
                "blocked_by_overlay_request_alignment": 1,
                "blocked_by_queue_contract": 1,
                "actionable_but_missing_queue_contract": 2,
                "ready_for_seat_execution": 2,
            },
        )
        self.assertEqual(payload["summary"]["execution_ready_seat_symbols"], ["GBPUSD", "USDJPY"])
        self.assertEqual(payload["summary"]["highest_execution_ready_symbol"], "GBPUSD")
        self.assertEqual(payload["summary"]["execution_gate_blocked_symbols"], ["BTCUSD", "EURUSD"])
        self.assertEqual(payload["summary"]["execution_contract_debt_symbols"], ["USDCAD", "NZDUSD"])

    def test_seat_queue_alignment_marks_earlier_queue_stage_as_preceding(self) -> None:
        status, read = board.seat_queue_alignment(
            seat_action="controlled_displacement_review",
            priority_context={
                "queue_task_id": "btc_restore_comparison_shadow",
                "queue_task_title": "Launch the BTC M15 warp restore comparison shadow",
                "queue_task_status": "ready",
                "queue_task_next_action_class": "control_shadow_and_collect_path_safety_evidence",
            },
        )

        self.assertEqual(status, "queue_ready_precedes_seat_call")
        self.assertIn("earlier-stage evidence contract", read)

    def test_seat_actionability_marks_preparatory_queue_stage_as_not_yet_actionable(self) -> None:
        status, read = board.seat_actionability(
            seat_action="controlled_displacement_review",
            priority_status="queue_ready",
            queue_alignment_status="queue_ready_precedes_seat_call",
        )

        self.assertEqual(status, "queue_ready_preparatory_only")
        self.assertIn("preparatory", read)

    def test_seat_contract_gap_marks_actionable_unqueued_rows(self) -> None:
        status, read = board.seat_contract_gap(
            actionability_status="local_actionable_unqueued",
            priority_status="unqueued_action",
        )

        self.assertEqual(status, "actionable_missing_queue_contract")
        self.assertIn("lacks a matching adaptive lab queue contract", read)

    def test_seat_overlay_contract_marks_preparatory_overlay_rows(self) -> None:
        status, read = board.seat_overlay_contract(
            adaptive_proof_row={
                "runtime_overlays": ["guard_open_admission", "cluster_aware_escape"],
                "runtime_overlay_read": "burst count is elevated",
            },
            actionability_status="queue_ready_preparatory_only",
        )

        self.assertEqual(status, "preparatory_overlay_contract")
        self.assertIn("burst count is elevated", read)

    def test_seat_overlay_launch_bridge_marks_unrequested_overlay_gap(self) -> None:
        status, read = board.seat_overlay_launch_bridge(
            adaptive_proof_row={
                "runtime_overlays": ["guard_open_admission", "cluster_aware_escape"],
            },
            runner_plan_row={
                "runtime_overlay_contract": {
                    "supported_overlays": ["guard_open_admission", "cluster_aware_escape"],
                    "requested_overlays": [],
                    "executable_overlays": [],
                    "unsupported_overlays": [],
                    "read": "Controller did not request any runtime overlays for this scaffold. This scaffold can currently express guard_open_admission, cluster_aware_escape when a future controller state requests them.",
                }
            },
        )

        self.assertEqual(status, "overlay_launch_bridge_supported_but_unrequested")
        self.assertIn("can express them", read)

    def test_seat_execution_gate_blocks_overlay_launch_debt(self) -> None:
        status, read = board.seat_execution_gate(
            contract_gap_status="queue_backed_actionable",
            overlay_contract_status="actionable_under_overlay_contract",
            overlay_launch_bridge_status="overlay_launch_bridge_supported_but_unrequested",
        )

        self.assertEqual(status, "blocked_by_overlay_request_alignment")
        self.assertIn("does not request", read)


if __name__ == "__main__":
    unittest.main()
