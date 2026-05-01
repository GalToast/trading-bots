#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_policy_gap_board as board


class HungryHippoPolicyGapBoardTests(unittest.TestCase):
    def test_classify_priority_promotes_cleared_positive_symbols(self) -> None:
        priority, score = board.classify_priority(
            "cleared_for_shadow_discussion",
            [],
            {"combined_net_usd": 854.82, "closes": 8352},
        )

        self.assertEqual(priority, "policy_seed_now")
        self.assertGreaterEqual(score, 70)

    def test_classify_priority_defers_negative_evidence(self) -> None:
        priority, score = board.classify_priority(
            "missing",
            [],
            {"combined_net_usd": -2546.80, "closes": 2478},
        )

        self.assertEqual(priority, "policy_defer")
        self.assertLess(score, 10)

    def test_build_payload_ranks_seed_now_before_seed_next(self) -> None:
        portability_payload = {
            "rows": [
                {
                    "symbol": "USDCHF",
                    "asset_class": "fx",
                    "generalization_status": "portable_missing_policy",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                    "hard_block_reasons": [],
                },
                {
                    "symbol": "AUDUSD",
                    "asset_class": "fx",
                    "generalization_status": "portable_missing_policy",
                    "deployment_verdict": "missing",
                    "hard_block_reasons": [],
                },
                {
                    "symbol": "EURJPY",
                    "asset_class": "fx",
                    "generalization_status": "portable_missing_policy",
                    "deployment_verdict": "missing",
                    "hard_block_reasons": [],
                },
            ]
        }
        apex_rows = {
            "USDCHF": {"source": "apex_doubler", "mode": "v3", "combined_net_usd": 854.82, "closes": 8352},
            "AUDUSD": {"source": "apex_doubler", "mode": "raw", "combined_net_usd": 2614.45, "closes": 2217},
            "EURJPY": {"source": "apex_doubler", "mode": "raw", "combined_net_usd": -2546.80, "closes": 2478},
        }

        payload = board.build_payload(portability_payload, apex_rows, {})

        self.assertEqual(payload["rows"][0]["symbol"], "USDCHF")
        self.assertEqual(payload["rows"][0]["priority"], "policy_seed_now")
        self.assertEqual(payload["rows"][1]["symbol"], "AUDUSD")
        self.assertEqual(payload["rows"][1]["priority"], "policy_seed_next")
        self.assertEqual(payload["rows"][2]["symbol"], "EURJPY")
        self.assertEqual(payload["rows"][2]["priority"], "policy_defer")
        self.assertEqual(payload["summary"]["policy_seed_now_symbols"], ["USDCHF"])
        self.assertEqual(payload["summary"]["policy_seed_next_symbols"], ["AUDUSD"])

    def test_render_markdown_contains_priority_rows(self) -> None:
        payload = {
            "generated_at": "2026-04-16T04:00:00+00:00",
            "leadership_read": ["Example"],
            "summary": {
                "missing_policy_symbol_count": 1,
                "policy_seed_now_symbols": ["USDCHF"],
                "policy_seed_next_symbols": [],
                "policy_defer_symbols": [],
            },
            "rows": [
                {
                    "symbol": "USDCHF",
                    "asset_class": "fx",
                    "priority": "policy_seed_now",
                    "deployment_verdict": "cleared_for_shadow_discussion",
                    "evidence_source": "apex_doubler",
                    "evidence_mode": "v3",
                    "evidence_net_usd": 854.82,
                    "evidence_closes": 8352,
                }
            ],
            "notes": ["Example note"],
        }

        markdown = board.render_markdown(payload)

        self.assertIn("Hungry Hippo Policy Gap Board", markdown)
        self.assertIn("policy_seed_now", markdown)
        self.assertIn("apex_doubler:v3", markdown)


if __name__ == "__main__":
    unittest.main()
