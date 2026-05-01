#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_claim_integrity_board as board


class CoinbaseClaimIntegrityBoardTests(unittest.TestCase):
    def test_summarize_volatility_validation_sums_losses(self) -> None:
        summary = board.summarize_volatility_validation(
            {
                "results": [
                    {"coin": "AAA-USD", "vol_breakout_best": {"pnl": 0.0}, "atr_trailing_best": {"pnl": -2.5}},
                    {"coin": "BBB-USD", "vol_breakout_best": {"pnl": -1.0}, "atr_trailing_best": {"pnl": -3.5}},
                ]
            }
        )

        self.assertEqual(summary["coins_tested"], 2)
        self.assertEqual(summary["vol_breakout_total_pnl"], -1.0)
        self.assertEqual(summary["atr_trailing_total_pnl"], -6.0)
        self.assertEqual(summary["fully_non_positive_coins"], ["AAA-USD", "BBB-USD"])

    def test_build_sup_overlap_row_requires_saved_report(self) -> None:
        row = board.build_sup_overlap_row()

        self.assertEqual(row["integrity_status"], "script_without_saved_report")
        self.assertEqual(row["evidence_class"], "script_only")
        self.assertIn("scripts/sup_overlap_analysis.py", row["source_paths"])

    def test_build_rave_freshness_row_prefers_forward_review(self) -> None:
        row = board.build_rave_freshness_row()

        self.assertIn(row["integrity_status"], {"superseded_by_fresher_sources", "artifact_backed"})
        self.assertTrue(row["governance_action"])
        self.assertIn("Forward review", row["summary"])
        self.assertIn("runtime board", row["claim"].lower())


if __name__ == "__main__":
    unittest.main()
