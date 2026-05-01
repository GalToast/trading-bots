#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_deployment_gate_board as board


class CoinbaseIsolatedRunnerDeploymentGateBoardTests(unittest.TestCase):
    def test_gate_board_holds_despite_infrastructure_readiness(self) -> None:
        payload = board.build_payload()
        rows = {row["subject"]: row for row in payload["rows"]}

        self.assertEqual(payload["summary"]["verdict"], "hold_for_governed_proof_completion")
        self.assertEqual(rows["infrastructure_base"]["status"], "ready")
        self.assertEqual(rows["strategy_book_governance"]["status"], "blocked")
        self.assertEqual(rows["override_path_signal_evidence"]["status"], "blocked")

    def test_gate_board_points_to_nom_as_next_governed_slot(self) -> None:
        payload = board.build_payload()
        self.assertEqual(payload["summary"]["next_governed_slot"], "NOM-USD")
        self.assertEqual(payload["summary"]["next_governed_strategy"], "range_breakout_shadow")


if __name__ == "__main__":
    unittest.main()
