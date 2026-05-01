#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_primary_runner_alignment_board as board


class CoinbasePrimaryRunnerAlignmentBoardTests(unittest.TestCase):
    def test_board_covers_all_primary_sleeves(self) -> None:
        rows = board.build_rows()

        self.assertEqual(len(rows), 7)
        self.assertEqual({row["coin"] for row in rows}, {"RAVE-USD", "A8-USD", "CFG-USD", "NOM-USD", "TRU-USD", "SUP-USD", "BAL-USD"})

    def test_rave_is_the_only_fully_aligned_live_primary(self) -> None:
        rows = board.build_rows()
        rave = next(row for row in rows if row["coin"] == "RAVE-USD")

        self.assertEqual(rave["alignment_status"], "aligned_active_saved_state")
        self.assertEqual(rave["planned_family"], "momentum")
        self.assertEqual(rave["saved_runner_family"], "momentum")

    def test_nom_and_sup_now_need_runtime_not_family_repair(self) -> None:
        rows = board.build_rows()
        by_coin = {row["coin"]: row for row in rows}

        self.assertEqual(by_coin["NOM-USD"]["saved_runner_family"], "range_breakout")
        self.assertEqual(by_coin["SUP-USD"]["saved_runner_family"], "range_breakout")
        self.assertEqual(by_coin["NOM-USD"]["alignment_status"], "aligned_config_needs_runtime_state")
        self.assertEqual(by_coin["SUP-USD"]["alignment_status"], "aligned_config_needs_runtime_state")

    def test_bal_is_now_config_aligned_but_runtime_stale(self) -> None:
        rows = board.build_rows()
        bal = next(row for row in rows if row["coin"] == "BAL-USD")

        self.assertEqual(bal["saved_runner_family"], "range_breakout")
        self.assertEqual(bal["alignment_status"], "aligned_config_legacy_runtime_present")


if __name__ == "__main__":
    unittest.main()
