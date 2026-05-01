#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_shared_pool_degradation_board as board


class CoinbaseSharedPoolDegradationBoardTests(unittest.TestCase):
    def test_rows_cover_all_primary_sleeves(self) -> None:
        rows = board.build_rows()

        self.assertEqual(len(rows), 7)
        self.assertEqual(
            {row["coin"] for row in rows},
            {"RAVE-USD", "A8-USD", "CFG-USD", "NOM-USD", "TRU-USD", "SUP-USD", "BAL-USD"},
        )

    def test_rave_and_tru_are_the_only_shared_survivors(self) -> None:
        rows = board.build_rows()
        survivors = {row["coin"] for row in rows if row["degradation_status"] == "shared_survivor"}

        self.assertEqual(survivors, {"RAVE-USD", "TRU-USD"})

    def test_nom_and_bal_are_flattened_not_demoted(self) -> None:
        rows = {row["coin"]: row for row in board.build_rows()}

        self.assertEqual(rows["NOM-USD"]["degradation_status"], "shared_pool_flattened")
        self.assertEqual(rows["BAL-USD"]["degradation_status"], "shared_pool_flattened")
        self.assertLess(abs(rows["NOM-USD"]["shared_runner_30d_net_usd"]), 1.0)
        self.assertLess(abs(rows["BAL-USD"]["shared_runner_30d_net_usd"]), 1.0)


if __name__ == "__main__":
    unittest.main()
