#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_team_leverage_execution_docket as board


class BuildTeamLeverageExecutionDocketTests(unittest.TestCase):
    def test_refresh_inputs_rebuilds_upstream_boards_in_order(self) -> None:
        with patch.object(board, "run_builder") as run_builder:
            board.refresh_inputs()

        run_builder.assert_has_calls(
            [
                call(board.ETH_BOARD_BUILDER),
                call(board.SHAPESHIFTER_BOARD_BUILDER),
                call(board.LATTICE_GAP_BOARD_BUILDER),
                call(board.LATTICE_PHASE1_COVERAGE_BUILDER),
                call(board.FX_PHASE1_VISIBILITY_BUILDER),
                call(board.FX_SHADOW_RECYCLE_BUILDER),
                call(board.FX_SHADOW_CONTRACT_DEBT_BUILDER),
                call(board.EXPERIMENTAL_BOARD_BUILDER),
            ]
        )

    def test_build_payload_orders_current_passive_proof_queue(self) -> None:
        payload = board.build_payload(
            {
                "overall_status": "waiting_market_proof",
                "eth_atr": {
                    "healthy_lane_count": 3,
                    "lane_count": 3,
                    "total_realized_closes": 0,
                    "total_open_positions": 0,
                    "latest_heartbeat_age_seconds": 11.6,
                },
            },
            {
                "active_rows": [
                    {"runner_pid": 4660},
                    {"runner_pid": 2296},
                    {"runner_pid": 24792},
                ]
            },
            {
                "proof_status": "historical_box_only",
                "readiness_verdict": "ready_for_shadow_review",
                "runner": {"fresh": True, "pid": 45108, "heartbeat_age_seconds": 5.1},
                "events": {
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count_since_runner_start": 0,
                },
                "economics": {"realized_closes": 12, "realized_net_usd": -158.28},
            },
            {
                "readiness": "telemetry_port_needed",
                "summary": {"missing_count": 8, "partial_count": 2, "present_count": 3},
                "next_action": "Port missing path telemetry.",
            },
            {
                "readiness": "awaiting_phase1_patch",
                "summary": {"field_count": 14, "covered_field_count": 0, "zero_coverage_field_count": 14},
                "next_action": "Apply the Task 29 runtime event enrichment, then rebuild this board and look for non-zero coverage in the open/close/rearm sections.",
            },
            {
                "tasks": [
                    {"id": 13, "status": "in_progress", "evidence": {"authoritative_surface": "reports/eth_atr_runtime_status_board.json", "monitoring_surface_repaired": True}},
                    {"id": 23, "status": "in_progress", "evidence": {}},
                    {
                        "id": 24,
                        "status": "todo",
                        "evidence": {
                            "implementation_status": "not_started",
                            "fx_verdict": "design complete but needs USDCHF lanes running first",
                            "current_fx_lanes": "EURUSD+GBPUSD only",
                            "blocking_dependency": "needs USDCHF or other inverse-correlated lane running",
                            "priority_downgraded": True,
                            "decision_6_executed": True,
                        },
                    },
                    {"id": 28, "status": "todo", "evidence": {}},
                ],
                "decisions": [
                    {
                        "id": 6,
                        "status": "done",
                        "recommended_option": "Demote from promotion queue and keep running only as closure-diagnosis control pair",
                        "evidence": {
                            "proof_status": "proof_negative",
                            "current_fx_watch_lead": "shadow_fx_close_policy_mixed_session_gated",
                        },
                    },
                    {
                        "id": 7,
                        "status": "open",
                        "recommended_option": "Option 2: include inventory-pressure and burst metrics in v1",
                    },
                ],
            },
            {
                "readiness": "contract_debt_clear",
                "summary": {},
            },
        )

        self.assertEqual(payload["rows"][0]["status"], "passive_monitor")
        self.assertEqual(payload["rows"][0]["workstream"], "ETH ATR first-close accumulation")
        self.assertEqual(payload["rows"][0]["current_blocker"], "market has not produced the first ETH ATR open or close yet")
        self.assertEqual(payload["rows"][0]["machine_truth"]["healthy_lane_count"], 3)
        self.assertEqual(payload["rows"][1]["workstream"], "Structure-shapeshifter fresh proof")
        self.assertEqual(payload["rows"][1]["current_blocker"], "no post-repair structure_flip or post-start box_geometry_adjust yet")
        self.assertEqual(payload["rows"][2]["status"], "start_now")
        self.assertEqual(payload["rows"][2]["workstream"], "Minimum lattice telemetry port")
        self.assertEqual(payload["rows"][2]["current_blocker"], "scope decision is already bounded, but runtime event coverage is still 0/14 so the patch has not reached diagnostic legibility yet")
        self.assertEqual(payload["rows"][2]["machine_truth"]["gap_board_missing_count"], 8)
        self.assertEqual(payload["rows"][2]["machine_truth"]["coverage_board_covered_field_count"], 0)
        self.assertEqual(payload["rows"][2]["machine_truth"]["coverage_board_expected_field_count"], 14)
        self.assertEqual(payload["rows"][3]["status"], "blocked_on_dependency")
        self.assertEqual(payload["rows"][3]["machine_truth"]["blocking_dependency"], "needs USDCHF or other inverse-correlated lane running")
        self.assertEqual(payload["rows"][4]["status"], "do_not_start")
        self.assertEqual(payload["rows"][4]["machine_truth"]["decision_id"], 6)
        self.assertEqual(payload["status_counts"]["start_now"], 1)
        self.assertEqual(payload["status_counts"]["passive_monitor"], 2)

    def test_build_payload_uses_dynamic_coverage_ratio_in_post_patch_text(self) -> None:
        payload = board.build_payload(
            {"overall_status": "waiting_post_restart_event", "eth_atr": {}},
            {"active_rows": []},
            {
                "proof_status": "historical_box_only",
                "readiness_verdict": "ready_for_shadow_review",
                "runner": {"fresh": True},
                "events": {},
                "economics": {},
            },
            {
                "readiness": "telemetry_surface_present",
                "summary": {"missing_count": 0, "partial_count": 0, "present_count": 14},
                "next_action": "Validate runtime reviewability.",
            },
            {
                "readiness": "stale_or_pre_enrichment_log",
                "summary": {"field_count": 18, "covered_field_count": 0, "zero_coverage_field_count": 18},
                "next_action": "Rebuild against a fresh post-enrichment log.",
                "deployment_context": {
                    "event_log_mtime": "2026-04-16T02:52:45.410299+00:00",
                    "reference_code_mtime": "2026-04-16T03:34:57.156082+00:00",
                    "event_log_is_newer_than_reference_code": False,
                },
            },
            {
                "tasks": [
                    {"id": 13, "status": "in_progress", "evidence": {}},
                    {"id": 23, "status": "in_progress", "evidence": {}},
                    {"id": 24, "status": "todo", "evidence": {"blocking_dependency": "needs USDCHF"}},
                    {"id": 28, "status": "completed", "evidence": {}},
                ],
                "decisions": [
                    {"id": 6, "status": "done", "recommended_option": "demoted", "evidence": {}},
                    {"id": 7, "status": "done", "recommended_option": "Option 2"},
                ],
            },
            {
                "readiness": "contract_debt_clear",
                "summary": {},
            },
        )

        self.assertIn("0/18", payload["leadership_read"][1])
        self.assertIn("already landed", payload["leadership_read"][1])
        self.assertEqual(payload["rows"][2]["status"], "passive_monitor")
        self.assertEqual(payload["rows"][2]["workstream"], "Post-patch lattice telemetry runtime evidence")
        self.assertEqual(
            payload["rows"][2]["current_blocker"],
            "the telemetry-bearing runners are already live, but the current runner window has not emitted a fresh enriched event yet so coverage is still stale-log 0/18",
        )
        self.assertEqual(
            payload["rows"][2]["required_evidence"][0],
            "the first fresh post-patch open_ticket or close/escape-like event lands in the current runner window",
        )
        self.assertEqual(
            payload["rows"][2]["required_evidence"][2],
            "a fresh post-enrichment event log moves the phase1 coverage board off 0/18 so the new fields are reviewable instead of stale-log only",
        )
        self.assertIn("without ordering another recycle", payload["rows"][2]["first_honest_outcome"])
        self.assertIn("Do not order another recycle", payload["rows"][2]["do_not_do_yet"])
        self.assertEqual(payload["status_counts"]["start_now"], 0)
        self.assertEqual(payload["status_counts"]["passive_monitor"], 3)
        self.assertFalse(payload["rows"][2]["machine_truth"]["coverage_board_event_log_is_newer_than_reference_code"])
        self.assertEqual(payload["rows"][2]["machine_truth"]["experimental_board_status"], "waiting_post_restart_event")

    def test_build_payload_exposes_shadow_only_acceleration_when_available(self) -> None:
        payload = board.build_payload(
            {"overall_status": "waiting_post_restart_event", "eth_atr": {}},
            {"active_rows": []},
            {
                "proof_status": "historical_box_only",
                "readiness_verdict": "ready_for_shadow_review",
                "runner": {"fresh": True},
                "events": {},
                "economics": {},
            },
            {
                "readiness": "telemetry_surface_present",
                "summary": {"missing_count": 0, "partial_count": 0, "present_count": 14},
            },
            {
                "readiness": "stale_or_pre_enrichment_log",
                "summary": {"field_count": 18, "covered_field_count": 0, "zero_coverage_field_count": 18},
                "deployment_context": {
                    "event_log_mtime": "2026-04-16T02:52:45.410299+00:00",
                    "reference_code_mtime": "2026-04-16T03:34:57.156082+00:00",
                    "event_log_is_newer_than_reference_code": False,
                },
            },
            {
                "tasks": [
                    {"id": 13, "status": "in_progress", "evidence": {}},
                    {"id": 23, "status": "in_progress", "evidence": {}},
                    {"id": 24, "status": "todo", "evidence": {"blocking_dependency": "needs USDCHF"}},
                    {"id": 28, "status": "completed", "evidence": {}},
                ],
                "decisions": [
                    {"id": 6, "status": "done", "recommended_option": "demoted", "evidence": {}},
                    {"id": 7, "status": "done", "recommended_option": "Option 2"},
                ],
            },
            {
                "readiness": "shadow_recycle_queue_ready",
                "summary": {
                    "recycle_candidate_count": 6,
                    "recycle_first_wave_count": 3,
                    "top_recycle_candidate": "shadow_xagusd_m15_warp",
                },
            },
            {
                "readiness": "contract_debt_actionable",
                "summary": {
                    "unlockable_first_wave_count": 2,
                    "projected_safe_first_wave_count": 5,
                    "top_unlock_candidate": "shadow_usdjpy_m15_warp",
                },
            },
        )

        self.assertIn("contract-debt board shows 2 more first-wave candidates", payload["rows"][2]["first_honest_outcome"])
        self.assertIn("suppressed first-wave rows as contract debt", payload["rows"][2]["do_not_do_yet"])
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_recycle_first_wave_count"], 3)
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_unlockable_first_wave_count"], 2)
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_projected_safe_first_wave_count"], 5)
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_top_unlock_candidate"], "shadow_usdjpy_m15_warp")
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_top_recycle_candidate"], "shadow_xagusd_m15_warp")
        self.assertIn("contract-debt board", payload["leadership_read"][2])

    def test_render_markdown_mentions_passive_monitor_status(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T03:10:00+00:00",
                "leadership_read": ["one"],
                "status_counts": {"start_now": 0, "passive_monitor": 1, "blocked_on_dependency": 0, "do_not_start": 0},
                "rows": [
                    {
                        "priority": 1,
                        "status": "passive_monitor",
                        "workstream": "ETH ATR first-close accumulation",
                        "lane": "eth lanes",
                        "why_high_leverage": "proof first",
                        "depends_on": [],
                        "current_blocker": "no close yet",
                        "required_evidence": ["first close"],
                        "first_honest_outcome": "proof arrives",
                        "unlocks": ["task 13"],
                        "machine_truth": {"task_id": 13},
                        "do_not_do_yet": "do not retune",
                    }
                ],
            }
        )

        self.assertIn("Team Leverage Execution Docket", markdown)
        self.assertIn("passive_monitor", markdown)
        self.assertIn("ETH ATR first-close accumulation", markdown)


if __name__ == "__main__":
    unittest.main()
