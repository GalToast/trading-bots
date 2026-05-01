#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_deployment_evidence_freshness_board as board


class CoinbaseDeploymentEvidenceFreshnessBoardTests(unittest.TestCase):
    def test_payload_emits_expected_verdict(self) -> None:
        payload = board.build_payload()

        self.assertEqual(payload["summary"]["verdict"], "fresh_but_not_go")
        self.assertEqual(len(payload["rows"]), 5)

    def test_gate_row_stays_blocking(self) -> None:
        rows = {row["subject"]: row for row in board.build_rows()}
        gate = rows["governed_deployment_gate"]

        self.assertEqual(gate["decision"], "governance_gate_is_current_and_blocking")
        self.assertIn("hold_for_governed_proof_completion", gate["evidence"])

    def test_tracker_row_marks_bounded_smoke_snapshot(self) -> None:
        rows = {row["subject"]: row for row in board.build_rows()}
        tracker = rows["tracker_snapshot"]

        self.assertEqual(tracker["status"], "bounded_smoke_snapshot")
        self.assertIn("total_closes=0", tracker["evidence"])


if __name__ == "__main__":
    unittest.main()
