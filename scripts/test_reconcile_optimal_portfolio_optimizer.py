#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import reconcile_optimal_portfolio_optimizer as reconcile_mod


class ReconcileOptimalPortfolioOptimizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = reconcile_mod.reconcile()
        cls.by_name = {row["scenario_name"]: row for row in cls.payload["scenarios"]}

    def test_small_cash_scenario_collapses_under_canonical_minimum(self) -> None:
        scenario = self.by_name["per_coin_5_33"]
        self.assertEqual(scenario["feasible_count"], 0)
        self.assertLess(scenario["canonical_total_pnl"], scenario["projected_total_pnl"])
        self.assertTrue(all(not row["feasible"] for row in scenario["assignment"].values()))

    def test_full_cash_scenario_keeps_all_coins_feasible(self) -> None:
        scenario = self.by_name["per_coin_100"]
        self.assertEqual(scenario["feasible_count"], scenario["coin_count"])
        self.assertNotEqual(scenario["canonical_total_pnl"], scenario["projected_total_pnl"])
        self.assertTrue(all(row["feasible"] for row in scenario["assignment"].values()))


if __name__ == "__main__":
    unittest.main()
