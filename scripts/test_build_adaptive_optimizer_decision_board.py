#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_optimizer_decision_board as board


class AdaptiveOptimizerDecisionBoardTests(unittest.TestCase):
    def test_both_optimizer_surfaces_are_dual_mode(self) -> None:
        payload = board.build_payload()
        rows = {row["surface_id"]: row for row in payload["rows"]}

        self.assertEqual(rows["allocation_optimizer"]["source_mode"], "native_inline_replay")
        self.assertEqual(rows["optimal_portfolio_optimizer"]["source_mode"], "native_inline_replay")
        self.assertEqual(payload["summary"]["dual_mode_surfaces"], 2)

    def test_portfolio_surface_carries_session_gate_drift(self) -> None:
        payload = board.build_payload()
        rows = {row["surface_id"]: row for row in payload["rows"]}
        drift = rows["optimal_portfolio_optimizer"]["drift_attribution"]

        self.assertGreater(float(drift["session_gate_off"]), 0.0)
        self.assertIn("per_coin_100", [item["label"] for item in rows["optimal_portfolio_optimizer"]["highlights"]])


if __name__ == "__main__":
    unittest.main()
