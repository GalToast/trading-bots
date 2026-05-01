#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runtime_proof_board as board


class CoinbaseIsolatedRuntimeProofBoardTests(unittest.TestCase):
    def test_rows_cover_non_rave_primaries(self) -> None:
        rows = board.build_rows()

        self.assertEqual(len(rows), 6)
        self.assertEqual(
            {row["coin"] for row in rows},
            {"A8-USD", "CFG-USD", "NOM-USD", "TRU-USD", "SUP-USD", "BAL-USD"},
        )

    def test_wave_1_items_stay_first(self) -> None:
        rows = board.build_rows()
        self.assertEqual(rows[0]["coin"], "A8-USD")
        self.assertEqual(rows[0]["proof_phase"], "artifact_cleanup_then_runtime")
        self.assertEqual(rows[1]["coin"], "CFG-USD")
        self.assertEqual(rows[1]["proof_phase"], "launch_isolated_runtime_now")

    def test_nom_sup_bal_are_marked_architecture_sensitive(self) -> None:
        rows = {row["coin"]: row for row in board.build_rows()}

        self.assertEqual(rows["NOM-USD"]["shared_degradation_status"], "shared_pool_flattened")
        self.assertEqual(rows["SUP-USD"]["shared_degradation_status"], "shared_pool_flattened")
        self.assertEqual(rows["BAL-USD"]["proof_phase"], "replace_legacy_runtime")


if __name__ == "__main__":
    unittest.main()
