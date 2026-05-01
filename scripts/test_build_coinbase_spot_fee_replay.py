#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_coinbase_spot_fee_replay import derive_quantity, fee_replay_net, summarize


class CoinbaseSpotFeeReplayTests(unittest.TestCase):
    def test_fee_replay_net_reprices_both_sides(self) -> None:
        result = fee_replay_net(entry_price=1.0, exit_price=1.02, quantity=10.0, fee_bps_per_side=120.0)
        self.assertAlmostEqual(result["gross_pnl"], 0.2)
        self.assertAlmostEqual(result["replayed_fee"], 0.2424)
        self.assertAlmostEqual(result["replayed_net_pnl"], -0.0424)

    def test_derive_quantity_from_gross_when_open_missing(self) -> None:
        qty = derive_quantity({"entry_price": 1.0, "exit_price": 1.02, "gross_pnl": 0.2})
        self.assertAlmostEqual(qty, 10.0)

    def test_summarize_reports_negative_replay(self) -> None:
        summary = summarize(
            [
                {"logged_net_pnl": 0.1, "replayed_net_pnl": -0.04, "replayed_fee": 0.24, "net_delta_vs_logged": -0.14},
                {"logged_net_pnl": 0.2, "replayed_net_pnl": 0.01, "replayed_fee": 0.25, "net_delta_vs_logged": -0.19},
            ]
        )
        self.assertEqual(summary["closes"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertAlmostEqual(summary["replayed_net_pnl"], -0.03)


if __name__ == "__main__":
    unittest.main()
