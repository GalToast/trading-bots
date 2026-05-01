#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_reconciliation as reconciliation


class BacktestReconciliationTests(unittest.TestCase):
    def test_equal_split_flags_infeasible_small_allocations(self) -> None:
        plan = reconciliation.equal_split_feasibility(48.0, 5)
        self.assertFalse(plan["feasible"])
        self.assertIn("no-trade artifact", plan["reason"])
        self.assertEqual(plan["capital_per_strategy"], 9.6)

    def test_equal_split_accepts_threshold_sized_allocations(self) -> None:
        plan = reconciliation.equal_split_feasibility(50.0, 5)
        self.assertTrue(plan["feasible"])
        self.assertEqual(plan["capital_per_strategy"], 10.0)

    def test_candle_snapshot_round_trip(self) -> None:
        payload = {
            "RAVE-USD": [{"start": "1", "open": "1.0", "high": "1.1", "low": "0.9", "close": "1.05"}],
            "IOTX-USD": [{"start": "2", "open": "0.1", "high": "0.2", "low": "0.09", "close": "0.15"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            reconciliation.save_candle_snapshot(path, payload)
            loaded = reconciliation.load_candle_snapshot(path)
        self.assertEqual(loaded, payload)


if __name__ == "__main__":
    unittest.main()
