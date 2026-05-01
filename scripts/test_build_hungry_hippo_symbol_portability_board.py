#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_symbol_portability_board as board


class HungryHippoSymbolPortabilityBoardTests(unittest.TestCase):
    def test_build_row_marks_missing_policy_when_guardrail_surface_is_missing(self) -> None:
        row = board.build_row(
            "DOGEUSD",
            deployment_row={"symbol": "DOGEUSD", "deployment_verdict": "hard_block"},
            guardrail_row=None,
            launch_rows=[],
        )

        self.assertEqual(row["generalization_status"], "portable_missing_policy")
        self.assertEqual(row["highest_leverage_gap"], "canonical_guardrail_and_regime")

    def test_build_row_marks_waiting_forward_proof_when_micro_step_is_only_blocker(self) -> None:
        row = board.build_row(
            "NAS100",
            deployment_row={
                "symbol": "NAS100",
                "deployment_verdict": "hard_block",
                "hard_block_reasons": ["micro_step_without_20_forward_closes"],
            },
            guardrail_row={"symbol": "NAS100", "status": "promotable_now"},
            launch_rows=[
                {
                    "symbol": "NAS100",
                    "scope": "shadow_candidate",
                    "verdict": "fail",
                    "hard_fail_reasons": ["atr_micro_step_without_forward_proof"],
                }
            ],
        )

        self.assertEqual(row["generalization_status"], "portable_waiting_forward_proof")
        self.assertEqual(row["highest_leverage_gap"], "forward_shadow_proof")

    def test_build_row_marks_guardrail_blocked_before_contract_repair(self) -> None:
        row = board.build_row(
            "GBPUSD",
            deployment_row={
                "symbol": "GBPUSD",
                "deployment_verdict": "hard_block",
                "hard_block_reasons": ["micro_step_without_20_forward_closes"],
            },
            guardrail_row={"symbol": "GBPUSD", "status": "contradiction"},
            launch_rows=[
                {
                    "symbol": "GBPUSD",
                    "scope": "shadow_candidate",
                    "verdict": "fail",
                    "hard_fail_reasons": ["fx_step_below_floor"],
                }
            ],
        )

        self.assertEqual(row["generalization_status"], "portable_guardrail_blocked")
        self.assertEqual(row["highest_leverage_gap"], "guardrail_alignment")

    def test_build_row_does_not_promote_cleared_deployment_when_guardrail_is_blocked(self) -> None:
        row = board.build_row(
            "USDCHF",
            deployment_row={
                "symbol": "USDCHF",
                "deployment_verdict": "cleared_for_shadow_discussion",
                "hard_block_reasons": [],
            },
            guardrail_row={"symbol": "USDCHF", "status": "blocked_by_guardrail"},
            launch_rows=[
                {
                    "symbol": "USDCHF",
                    "scope": "shadow_candidate",
                    "verdict": "research_only",
                    "hard_fail_reasons": [],
                }
            ],
        )

        self.assertEqual(row["generalization_status"], "portable_guardrail_blocked")
        self.assertEqual(row["highest_leverage_gap"], "guardrail_alignment")

    def test_build_payload_summarizes_status_counts(self) -> None:
        deployment_gate = {
            "rows": [
                {
                    "symbol": "AUDUSD",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                    "hard_block_reasons": [],
                    "manual_review_reasons": [],
                },
                {
                    "symbol": "NAS100",
                    "deployment_verdict": "hard_block",
                    "hard_block_reasons": ["micro_step_without_20_forward_closes"],
                    "manual_review_reasons": [],
                },
                {
                    "symbol": "SOLUSD",
                    "deployment_verdict": "hard_block",
                    "hard_block_reasons": ["uncovered"],
                    "manual_review_reasons": [],
                },
            ]
        }
        guardrail_audit = {
            "rows": [
                {"symbol": "AUDUSD", "status": "promotable_now"},
                {"symbol": "NAS100", "status": "promotable_now"},
            ]
        }
        launch_safety = {
            "rows": [
                {"symbol": "AUDUSD", "scope": "shadow_candidate", "verdict": "research_only"},
                {"symbol": "NAS100", "scope": "shadow_candidate", "verdict": "fail", "hard_fail_reasons": ["atr_micro_step_without_forward_proof"]},
            ]
        }

        payload = board.build_payload(deployment_gate, guardrail_audit, launch_safety)
        summary = payload["summary"]

        self.assertEqual(summary["symbol_count"], 3)
        self.assertEqual(summary["family_portable_count"], 3)
        self.assertEqual(summary["surface_coverage_complete_count"], 2)
        self.assertEqual(summary["ready_for_shadow_discussion_symbols"], ["AUDUSD"])
        self.assertEqual(summary["waiting_forward_proof_symbols"], ["NAS100"])
        self.assertEqual(summary["missing_policy_symbols"], ["SOLUSD"])
        self.assertEqual(summary["status_counts"]["ready_for_shadow_discussion"], 1)
        self.assertEqual(summary["status_counts"]["portable_waiting_forward_proof"], 1)
        self.assertEqual(summary["status_counts"]["portable_missing_policy"], 1)

    def test_render_markdown_includes_portability_summary(self) -> None:
        payload = {
            "generated_at": "2026-04-16T04:00:00+00:00",
            "leadership_read": ["Example read."],
            "summary": {
                "symbol_count": 1,
                "family_portable_count": 1,
                "surface_coverage_complete_count": 1,
                "ready_for_shadow_discussion_symbols": ["AUDUSD"],
                "waiting_forward_proof_symbols": [],
                "missing_policy_symbols": [],
            },
            "rows": [
                {
                    "symbol": "AUDUSD",
                    "asset_class": "fx",
                    "generalization_status": "ready_for_shadow_discussion",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                    "guardrail_status": "promotable_now",
                    "launch_contract_count": 1,
                    "live_surface_count": 1,
                    "hard_block_reasons": [],
                    "launch_contract_fail_reasons": [],
                    "manual_review_reasons": [],
                    "highest_leverage_gap": "fresh_forward_proof",
                }
            ],
            "notes": ["Example note."],
        }

        markdown = board.render_markdown(payload)

        self.assertIn("Hungry Hippo Symbol Portability Board", markdown)
        self.assertIn("ready_for_shadow_discussion", markdown)
        self.assertIn("candidate=1 / live_surface=1", markdown)


if __name__ == "__main__":
    unittest.main()
