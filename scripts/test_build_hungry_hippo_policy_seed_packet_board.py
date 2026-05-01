#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_policy_seed_packet_board as board


class HungryHippoPolicySeedPacketBoardTests(unittest.TestCase):
    def test_choose_seed_action_distinguishes_missing_surfaces(self) -> None:
        self.assertEqual(
            board.choose_seed_action(regime_present=False, selector_present=False, rearm_present=True)[0],
            "seed_regime_and_selector",
        )
        self.assertEqual(
            board.choose_seed_action(regime_present=True, selector_present=False, rearm_present=True)[0],
            "seed_selector_profile_from_regime_truth",
        )

    def test_build_row_uses_existing_surfaces_and_family_defaults(self) -> None:
        row = board.build_row(
            {
                "symbol": "USDJPY",
                "asset_class": "fx",
                "priority": "policy_seed_next",
                "priority_score": 60,
                "evidence_source": "apex_doubler",
                "evidence_mode": "v3",
                "evidence_net_usd": 885.38,
                "evidence_closes": 13202,
            },
            regime_rows={"USDJPY": {"symbol": "USDJPY", "control_mode": "bounce_reversal", "action_bias": "SELL"}},
            rearm_rows={"USDJPY": {"symbol": "USDJPY", "canonical_guardrail_status": "aligned", "rearm_variant": "exc1"}},
            selector_rows={},
        )

        self.assertTrue(row["regime_row_present"])
        self.assertFalse(row["selector_row_present"])
        self.assertTrue(row["rearm_row_present"])
        self.assertEqual(row["suggested_seed_action"], "seed_selector_profile_from_regime_truth")
        self.assertEqual(row["family_default_timeframe"], "M15")

    def test_build_payload_summarizes_missing_surface_lists(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "symbol": "USDCHF",
                        "asset_class": "fx",
                        "priority": "policy_seed_now",
                        "priority_score": 90,
                        "evidence_source": "apex_doubler",
                        "evidence_mode": "v3",
                    },
                    {
                        "symbol": "AUDUSD",
                        "asset_class": "fx",
                        "priority": "policy_seed_next",
                        "priority_score": 31,
                        "evidence_source": "apex_doubler",
                        "evidence_mode": "raw",
                    },
                ]
            },
            {"rows": [{"symbol": "AUDUSD", "control_mode": "wait_extreme_confirmation", "action_bias": "NEUTRAL"}]},
            {"current_state_rearm_params": {"USDCHF": {"symbol": "USDCHF"}, "AUDUSD": {"symbol": "AUDUSD"}}},
            {"symbol_configs": {}},
        )

        summary = payload["summary"]
        self.assertEqual(summary["policy_seed_now_symbols"], ["USDCHF"])
        self.assertEqual(summary["policy_seed_next_symbols"], ["AUDUSD"])
        self.assertEqual(summary["missing_regime_symbols"], ["USDCHF"])
        self.assertEqual(summary["missing_selector_symbols"], ["USDCHF", "AUDUSD"])

    def test_render_markdown_includes_seed_actions(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:00:00+00:00",
                "leadership_read": ["Example"],
                "summary": {
                    "symbol_count": 1,
                    "policy_seed_now_symbols": ["USDCHF"],
                    "policy_seed_next_symbols": [],
                    "missing_regime_symbols": ["USDCHF"],
                    "missing_selector_symbols": ["USDCHF"],
                },
                "rows": [
                    {
                        "symbol": "USDCHF",
                        "priority": "policy_seed_now",
                        "regime_row_present": False,
                        "selector_row_present": False,
                        "rearm_row_present": True,
                        "suggested_seed_action": "seed_regime_and_selector",
                        "family_default_timeframe": "M15",
                        "family_default_base_step": 0.0004,
                        "family_default_max_open_per_side": 12,
                        "family_default_session_window": "06:00-10:00+13:00-17:00",
                    }
                ],
                "notes": ["Example note"],
            }
        )

        self.assertIn("Hungry Hippo Policy Seed Packet Board", markdown)
        self.assertIn("seed_regime_and_selector", markdown)


if __name__ == "__main__":
    unittest.main()
