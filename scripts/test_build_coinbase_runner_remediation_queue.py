#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_runner_remediation_queue as board


class CoinbaseRunnerRemediationQueueTests(unittest.TestCase):
    def test_queue_has_expected_item_counts(self) -> None:
        payload = board.build_payload()

        self.assertEqual(payload["summary"]["total_fix_items"], 9)
        self.assertEqual(payload["summary"]["wave_1_items"], 2)
        self.assertEqual(payload["summary"]["wave_2_items"], 4)
        self.assertEqual(payload["summary"]["reserve_items"], 2)

    def test_wave_1_fix_order_puts_a8_before_cfg(self) -> None:
        rows = board.build_rows()
        a8 = next(row for row in rows if row["coin"] == "A8-USD")
        cfg = next(row for row in rows if row["coin"] == "CFG-USD")

        self.assertEqual(a8["fix_order"], 2)
        self.assertEqual(cfg["fix_order"], 3)
        self.assertEqual(a8["remediation_phase"], "clear_wave_1_blocker")

    def test_nom_and_bal_runtime_repairs_stay_ahead_of_reserve_items(self) -> None:
        rows = board.build_rows()
        nom_primary = next(row for row in rows if row["coin"] == "NOM-USD" and row["launch_wave"] != "reserve")
        bal = next(row for row in rows if row["coin"] == "BAL-USD")
        nom_reserve = next(row for row in rows if row["coin"] == "NOM-USD" and row["launch_wave"] == "reserve")

        self.assertEqual(nom_primary["remediation_phase"], "clear_wave_2_runtime_gap")
        self.assertEqual(bal["remediation_phase"], "clear_wave_2_runtime_refresh")
        self.assertGreater(nom_reserve["fix_order"], bal["fix_order"])


if __name__ == "__main__":
    unittest.main()
