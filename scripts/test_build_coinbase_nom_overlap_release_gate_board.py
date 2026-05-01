#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_nom_overlap_release_gate_board as board


class CoinbaseNomOverlapReleaseGateBoardTests(unittest.TestCase):
    def test_nom_release_gate_holds_while_alt_lane_active(self) -> None:
        payload = board.build_payload()
        rows = {row["subject"]: row for row in payload["rows"]}

        self.assertEqual(payload["summary"]["release_verdict"], "hold_until_parallel_nom_lane_clears")
        self.assertEqual(rows["same_coin_admission"]["status"], "ready")
        self.assertEqual(rows["override_path_release"]["status"], "deferred")
        self.assertEqual(rows["parallel_nom_lane_conflict"]["status"], "active_conflict")

    def test_nom_gate_preserves_breakout_primary_shape(self) -> None:
        payload = board.build_payload()
        rows = {row["subject"]: row for row in payload["rows"]}

        self.assertEqual(rows["primary_secondary_shape"]["decision"], "keep_breakout_primary_momentum_secondary")
        self.assertEqual(payload["summary"]["next_release_action"], "wait_for_nom_alt_lane_flat_then_run_governed_nom_probe")


if __name__ == "__main__":
    unittest.main()
