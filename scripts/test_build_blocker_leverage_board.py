#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_blocker_leverage_board as board


class BuildBlockerLeverageBoardTests(unittest.TestCase):
    def test_refresh_inputs_rebuilds_upstream_boards_in_order(self) -> None:
        with patch.object(board, "run_builder") as run_builder:
            board.refresh_inputs()

        run_builder.assert_has_calls(
            [
                call(board.ETH_BOARD_BUILDER),
                call(board.SHAPESHIFTER_BOARD_BUILDER),
                call(board.LATTICE_PHASE1_COVERAGE_BUILDER),
                call(board.FX_PHASE1_VISIBILITY_BUILDER),
                call(board.FX_SHADOW_RECYCLE_BUILDER),
                call(board.FX_SHADOW_CONTRACT_DEBT_BUILDER),
                call(board.EXPERIMENTAL_BOARD_BUILDER),
            ]
        )

    def test_build_payload_ranks_current_passive_proof_blockers(self) -> None:
        payload = board.build_payload(
            {
                "overall_status": "waiting_market_proof",
                "eth_atr": {
                    "healthy_lane_count": 3,
                    "lane_count": 3,
                    "total_realized_closes": 0,
                    "total_open_positions": 0,
                },
            },
            {},
            {
                "proof_status": "historical_box_only",
                "runner": {"fresh": True},
                "events": {
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count_since_runner_start": 0,
                },
            },
            {
                "readiness": "telemetry_surface_present",
            },
            {
                "readiness": "awaiting_phase1_patch",
                "summary": {"field_count": 14, "covered_field_count": 0, "zero_coverage_field_count": 14},
            },
            {
                "tasks": [
                    {"id": 13, "status": "in_progress", "evidence": {}},
                    {"id": 23, "status": "in_progress", "evidence": {}},
                    {
                        "id": 24,
                        "status": "todo",
                        "evidence": {
                            "implementation_status": "not_started",
                            "priority_downgraded": True,
                            "fx_verdict": "design complete but needs USDCHF lanes running first",
                            "current_fx_lanes": "EURUSD+GBPUSD only",
                            "blocking_dependency": "needs USDCHF or other inverse-correlated lane running",
                        },
                    },
                ]
            },
            {
                "readiness": "contract_debt_clear",
                "summary": {},
            },
        )

        self.assertEqual(payload["rows"][0]["blocker"], "First ETH ATR market event")
        self.assertEqual(payload["rows"][0]["machine_truth"]["task_id"], 13)
        self.assertEqual(payload["rows"][1]["blocker"], "First post-repair shapeshifter proof event")
        self.assertEqual(payload["rows"][1]["machine_truth"]["proof_status"], "historical_box_only")
        self.assertEqual(payload["rows"][2]["blocker"], "Task 28 runtime event coverage still 0/14")
        self.assertEqual(payload["rows"][2]["machine_truth"]["coverage_covered_field_count"], 0)
        self.assertIn("code-present", payload["rows"][2]["current_queue_effect_if_unresolved"][0])
        self.assertEqual(payload["rows"][3]["blocker"], "Inverse-correlated FX lane for cross-symbol hedging")
        self.assertEqual(payload["rows"][3]["machine_truth"]["blocking_dependency"], "needs USDCHF or other inverse-correlated lane running")
        self.assertEqual(payload["dependency_summary"][0]["current_leverage"], "L2")

    def test_build_payload_uses_dynamic_coverage_ratio_in_ranked_blocker_text(self) -> None:
        payload = board.build_payload(
            {"overall_status": "waiting_post_restart_event", "eth_atr": {}},
            {},
            {"proof_status": "historical_box_only", "runner": {"fresh": True}, "events": {}},
            {"readiness": "telemetry_surface_present"},
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
                ]
            },
            {
                "readiness": "contract_debt_clear",
                "summary": {},
            },
        )

        self.assertIn("0/18", payload["leadership_read"][1])
        self.assertIn("first fresh post-patch event window", payload["leadership_read"][1])
        self.assertEqual(payload["rows"][2]["blocker"], "Task 28 runtime event coverage still 0/18 on a pre-enrichment log")
        self.assertIn("code work is already done", payload["rows"][2]["why_it_is_third"])
        self.assertEqual(payload["rows"][2]["current_blocker"][0], "coverage readiness is `stale_or_pre_enrichment_log` and the reviewed event log predates the telemetry-bearing core code")
        self.assertEqual(payload["rows"][2]["current_blocker"][1], "covered phase1 fields are `0/18`")
        self.assertIn("landed but blocked on the first fresh post-patch event window", payload["rows"][2]["current_queue_effect_if_unresolved"][0])
        self.assertEqual(payload["rows"][2]["honest_next_move"][0], "keep the current post-patch runners alive until a fresh enriched open or close-like event lands in the log")
        self.assertIn("after the first fresh event", payload["rows"][2]["honest_next_move"][1])
        self.assertEqual(payload["rows"][2]["machine_truth"]["experimental_board_status"], "waiting_post_restart_event")
        self.assertFalse(payload["rows"][2]["machine_truth"]["coverage_event_log_is_newer_than_reference_code"])

    def test_build_payload_mentions_optional_shadow_acceleration_when_available(self) -> None:
        payload = board.build_payload(
            {"overall_status": "waiting_post_restart_event", "eth_atr": {}},
            {},
            {"proof_status": "historical_box_only", "runner": {"fresh": True}, "events": {}},
            {"readiness": "telemetry_surface_present"},
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
                ]
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

        self.assertIn("suppressed by restart-contract debt", payload["rows"][2]["why_it_is_third"])
        self.assertIn("fx_shadow_telemetry_recycle_board", payload["rows"][2]["authoritative_reports"][2])
        self.assertIn("fx_shadow_telemetry_contract_debt_board", payload["rows"][2]["authoritative_reports"][3])
        self.assertIn("shadow_xagusd_m15_warp", payload["rows"][2]["honest_next_move"][1])
        self.assertIn("shadow_usdjpy_m15_warp", payload["rows"][2]["honest_next_move"][1])
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_recycle_first_wave_count"], 3)
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_unlockable_first_wave_count"], 2)
        self.assertEqual(payload["rows"][2]["machine_truth"]["fx_shadow_projected_safe_first_wave_count"], 5)

    def test_render_markdown_mentions_dependency_summary(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T03:10:00+00:00",
                "leadership_read": ["one"],
                "rows": [
                    {
                        "priority": 1,
                        "blocker": "First ETH ATR market event",
                        "leverage_tier": "L2",
                        "why_it_is_first": "proof gate",
                        "current_blocker": ["zero closes"],
                        "authoritative_reports": ["reports/experimental_proof_watch_board.md"],
                        "unlocks": ["task 13"],
                        "current_queue_effect_if_unresolved": ["stay passive"],
                        "honest_next_move": ["wait"],
                        "machine_truth": {"task_id": 13},
                    }
                ],
                "dependency_summary": [
                    {"blocker": "First ETH ATR market event", "unlock_count": 1, "current_leverage": "L2"}
                ],
            }
        )

        self.assertIn("Blocker Leverage Board", markdown)
        self.assertIn("Dependency Summary", markdown)
        self.assertIn("First ETH ATR market event", markdown)


if __name__ == "__main__":
    unittest.main()
