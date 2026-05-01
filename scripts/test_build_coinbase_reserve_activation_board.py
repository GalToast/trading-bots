#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_reserve_activation_board as board


class CoinbaseReserveActivationBoardTests(unittest.TestCase):
    def test_rows_cover_nom_rave_sup_and_bal(self) -> None:
        rows = board.build_rows()
        coins = [row["coin"] for row in rows]

        self.assertEqual(coins, ["NOM-USD", "RAVE-USD", "SUP-USD", "BAL-USD"])

    def test_nom_is_the_first_ready_reserve_candidate(self) -> None:
        rows = board.build_rows()
        nom = rows[0]

        self.assertEqual(nom["coin"], "NOM-USD")
        self.assertEqual(nom["reserve_status"], "ready_when_reserve_exists")
        self.assertGreater(nom["combined_uplift_vs_best_single"], 1000.0)

    def test_rave_remains_blocked_by_runtime_and_graduation(self) -> None:
        rows = board.build_rows()
        rave = next(row for row in rows if row["coin"] == "RAVE-USD")

        self.assertEqual(rave["reserve_status"], "blocked_runtime_and_graduation")
        self.assertEqual(rave["runtime_dependency_status"], "offline")
        self.assertGreaterEqual(rave["missing_proof_count"], 3)


if __name__ == "__main__":
    unittest.main()
