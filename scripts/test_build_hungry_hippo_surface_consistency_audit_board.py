#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_surface_consistency_audit_board as board


class BuildHungryHippoSurfaceConsistencyAuditBoardTests(unittest.TestCase):
    def test_build_payload_flags_stale_rollout_and_packet(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {"symbol": "USDCAD", "generalization_status": "ready_for_shadow_discussion"},
                    {"symbol": "XRPUSD", "generalization_status": "ready_for_shadow_discussion"},
                    {"symbol": "AUDUSD", "generalization_status": "ready_for_shadow_discussion"},
                ]
            },
            {
                "rows": [
                    {"symbol": "USDCAD", "runtime_state": "not_launched_yet"},
                    {"symbol": "XRPUSD", "runtime_state": "not_launched_yet"},
                    {"symbol": "AUDUSD", "runtime_state": "not_launched_yet"},
                ]
            },
            {
                "rows": [
                    {
                        "max_active_lanes": 1,
                        "current_status": "blocked_until_slot1_forward_proof",
                        "machine_truth": {"starter_candidate_symbol": "USDCAD"},
                    },
                    {
                        "max_active_lanes": 2,
                        "current_status": "blocked_missing_launch_contract_followthrough",
                        "machine_truth": {
                            "slot2_symbol": "XRPUSD",
                            "slot2_generalization_status": "ready_for_shadow_discussion",
                        },
                    },
                    {
                        "max_active_lanes": 3,
                        "current_status": "blocked_until_slot1_and_slot2_are_resolved",
                        "machine_truth": {
                            "slot3_symbol": "EURJPY",
                            "slot3_portability_status": "portable_missing_launch_contract",
                        },
                    },
                ]
            },
            {
                "rows": [
                    {"symbol": "USDCAD", "launch_readiness": "launch_now"},
                    {"symbol": "XRPUSD", "launch_readiness": "hold_until_prior_gate_clears"},
                    {"symbol": "AUDUSD", "launch_readiness": "watch_only_outside_current_unlock_ladder"},
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["watch_set_symbols"], ["USDCAD", "XRPUSD", "AUDUSD"])
        self.assertIn("XRPUSD", summary["stale_symbols"])
        self.assertNotIn("AUDUSD", summary["stale_symbols"])

        rows = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(rows["USDCAD"]["verdict"], "aligned_launch_now")
        self.assertEqual(rows["XRPUSD"]["verdict"], "stale_rollout_gate_and_packet")
        self.assertEqual(rows["AUDUSD"]["verdict"], "aligned_watch_only_outside_unlock_ladder")

    def test_render_markdown_mentions_stale_symbols(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["a"],
                "summary": {
                    "symbol_count": 2,
                    "verdict_counts": {"stale_rollout_gate": 1},
                    "stale_symbols": ["AUDUSD"],
                    "watch_set_symbols": ["AUDUSD", "USDCAD"],
                },
                "rows": [
                    {
                        "symbol": "AUDUSD",
                        "portability_status": "ready_for_shadow_discussion",
                        "watch_present": True,
                        "watch_runtime_state": "not_launched_yet",
                        "rollout_slot": 3,
                        "launch_packet_present": False,
                        "launch_packet_readiness": "",
                        "verdict": "stale_rollout_gate",
                        "rationale": "r",
                    }
                ],
            }
        )

        self.assertIn("Hungry Hippo Surface Consistency Audit Board", markdown)
        self.assertIn("Stale symbols: `['AUDUSD']`", markdown)
        self.assertIn("`stale_rollout_gate`", markdown)

    def test_build_payload_flags_rollout_vs_validation_conflict(self) -> None:
        payload = board.build_payload(
            {"rows": [{"symbol": "USDCAD", "generalization_status": "ready_for_shadow_discussion"}]},
            {"rows": []},
            {
                "rows": [
                    {
                        "max_active_lanes": 1,
                        "current_status": "blocked_until_slot1_forward_proof",
                        "machine_truth": {"starter_candidate_symbol": "USDCAD"},
                    }
                ]
            },
            {
                "rows": [
                    {"symbol": "USDCAD", "launch_readiness": "blocked_validation_fail"},
                ]
            },
        )

        self.assertEqual(payload["summary"]["stale_symbols"], ["USDCAD"])
        self.assertEqual(payload["rows"][0]["verdict"], "stale_rollout_gate_vs_validation")


if __name__ == "__main__":
    unittest.main()
