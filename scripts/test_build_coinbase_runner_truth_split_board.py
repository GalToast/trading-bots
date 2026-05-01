#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_runner_truth_split_board as board


class CoinbaseRunnerTruthSplitBoardTests(unittest.TestCase):
    def test_rows_cover_primary_sleeves(self) -> None:
        rows = board.build_rows()

        self.assertEqual(len(rows), 7)
        self.assertEqual({row["coin"] for row in rows}, {"RAVE-USD", "A8-USD", "CFG-USD", "TRU-USD", "NOM-USD", "SUP-USD", "BAL-USD"})

    def test_nom_and_sup_are_aligned_but_still_missing_runtime(self) -> None:
        rows = {row["coin"]: row for row in board.build_rows()}

        self.assertEqual(rows["NOM-USD"]["source_family"], "range_breakout")
        self.assertEqual(rows["SUP-USD"]["source_family"], "range_breakout")
        self.assertEqual(rows["NOM-USD"]["saved_backfill_family"], "range_breakout")
        self.assertEqual(rows["SUP-USD"]["saved_backfill_family"], "range_breakout")
        self.assertEqual(rows["NOM-USD"]["truth_status"], "source_aligned_saved_runtime_missing")
        self.assertEqual(rows["SUP-USD"]["truth_status"], "source_aligned_saved_runtime_missing")

    def test_bal_is_aligned_in_backfill_but_runtime_stale(self) -> None:
        rows = {row["coin"]: row for row in board.build_rows()}

        self.assertEqual(rows["BAL-USD"]["source_family"], "range_breakout")
        self.assertEqual(rows["BAL-USD"]["saved_backfill_family"], "range_breakout")
        self.assertEqual(rows["BAL-USD"]["truth_status"], "source_aligned_saved_runtime_stale")

    def test_rave_remains_the_only_fully_aligned_row(self) -> None:
        rows = board.build_rows()
        aligned = [row for row in rows if row["truth_status"] == "source_and_saved_live_aligned"]

        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned[0]["coin"], "RAVE-USD")


if __name__ == "__main__":
    unittest.main()
