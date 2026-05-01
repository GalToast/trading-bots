import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import reconcile_allocation_optimizer as mod


class ReconcileAllocationOptimizerTests(unittest.TestCase):
    def test_equal_split_is_infeasible_under_canonical_min_entry_cash(self) -> None:
        payload = mod.reconcile()
        plans = {plan["plan_name"]: plan for plan in payload["plans"]}
        equal_split = plans["equal_split"]
        self.assertEqual(equal_split["feasible_count"], 0)
        self.assertLess(equal_split["canonical_total_pnl"], equal_split["projected_total_pnl"])

    def test_optimized_plan_has_single_feasible_coin(self) -> None:
        payload = mod.reconcile()
        plans = {plan["plan_name"]: plan for plan in payload["plans"]}
        optimized = plans["optimized"]
        self.assertEqual(optimized["feasible_count"], 1)
        self.assertIn("NOM-USD", optimized["per_coin"])
        self.assertTrue(optimized["per_coin"]["GHST-USD"]["reason"].startswith("allocation $2.00 below canonical"))


if __name__ == "__main__":
    unittest.main()
