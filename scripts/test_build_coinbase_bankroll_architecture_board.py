#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_bankroll_architecture_board as board


class CoinbaseBankrollArchitectureBoardTests(unittest.TestCase):
    def test_build_rows_contains_core_architecture_cases(self) -> None:
        rows = board.build_rows()
        architectures = {row["architecture"] for row in rows}

        self.assertIn("shared_cross_coin_best_of_router", architectures)
        self.assertIn("isolated_per_coin_verified_aggregate", architectures)
        self.assertIn("niche_shared_bankroll_sniper_grinder", architectures)

    def test_shared_cross_coin_case_is_rejected(self) -> None:
        rows = board.build_rows()
        shared = next(row for row in rows if row["architecture"] == "shared_cross_coin_best_of_router")

        self.assertEqual(shared["status"], "reject_naive_shared_bankroll")
        self.assertLessEqual(shared["net_pnl_usd"], -48.0)
        self.assertEqual(shared["capital_base_usd"], 48.0)

    def test_leadership_read_recommends_isolated_default(self) -> None:
        payload = board.build_payload()
        text = " ".join(payload["leadership_read"]).lower()

        self.assertIn("isolated per-coin sleeves", text)
        self.assertIn("shared bankroll", text)


if __name__ == "__main__":
    unittest.main()
