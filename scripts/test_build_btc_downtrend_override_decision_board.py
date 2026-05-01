#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_downtrend_override_decision_board as board


class BuildBtcDowntrendOverrideDecisionBoardTests(unittest.TestCase):
    def test_build_payload_prefers_conform_to_handoff(self) -> None:
        recon = {
            "summary": {"status": "needs_reconcile"},
            "comparisons": [
                {"field": "step_buy", "handoff": 389.14393, "config": 389.14},
                {"field": "step_sell", "handoff": 129.71464, "config": 129.71},
                {"field": "rearm_variant", "handoff": "rearm_lvl2_exc1", "config": "rearm_lvl2_exc2"},
                {"field": "max_open_per_side", "handoff": 6, "config": 12},
                {"field": "enabled", "handoff": False, "config": True},
            ],
        }
        promotion_gate = {
            "rows": [
                {
                    "candidate": "BTCUSD M15 sell-tight downtrend shape",
                    "promotion_verdict": "reconcile_shadow_then_judge",
                    "current_stage": "shadow_config_exists_needs_reconcile",
                }
            ]
        }
        rubric = {
            "rows": [
                {
                    "candidate": "BTCUSD M15 sell-tight downtrend shape",
                    "shadow_to_live_rubric": {"required_config_state": "reconciled_shadow_config"},
                }
            ]
        }

        payload = board.build_payload(recon, promotion_gate, rubric)

        self.assertEqual(payload["recommendation"]["preferred_option"], "conform_to_handoff")
        self.assertEqual(payload["current_truth"]["current_stage"], "shadow_config_exists_needs_reconcile")
        self.assertEqual(payload["decision_options"][0]["option"], "conform_to_handoff")

    def test_render_markdown_mentions_options(self) -> None:
        payload = {
            "generated_at": "2026-04-15T15:00:00+00:00",
            "leadership_read": ["one"],
            "current_truth": {"reconciliation_status": "needs_reconcile"},
            "decision_options": [
                {"option": "conform_to_handoff", "what_changes": {"enabled": False}, "benefits": ["clean"], "costs": ["more conservative"]},
                {"option": "ratify_current_override", "what_changes": {"handoff_or_governance_target": {"enabled": True}}},
            ],
            "recommendation": {"preferred_option": "conform_to_handoff", "why": ["pre-proof"]},
        }

        markdown = board.render_markdown(payload)
        self.assertIn("BTC Downtrend Override Decision Board", markdown)
        self.assertIn("conform_to_handoff", markdown)
        self.assertIn("ratify_current_override", markdown)


if __name__ == "__main__":
    unittest.main()
