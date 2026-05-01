#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_shadow_trade_forensics as board


class CoinbaseSpotShadowTradeForensicsTests(unittest.TestCase):
    def test_piranha_close_metrics_reconstructs_fee_adjusted_net(self) -> None:
        event = {"entry_price": 1.0, "exit_price": 1.1, "quantity": 10.0, "realized_pnl": 0.874}
        metrics = board.piranha_close_metrics(event, fee_bps_per_side=60.0)
        self.assertAlmostEqual(metrics["gross_pnl"], 1.0)
        self.assertAlmostEqual(metrics["fee"], 0.126)
        self.assertEqual(metrics["net_pnl"], 0.874)
        self.assertEqual(metrics["exit_reason"], "profit_target")

    def test_summarize_closes_reports_win_rate_and_worst(self) -> None:
        summary = board.summarize_closes(
            [
                {"net_pnl": 1.0, "gross_pnl": 1.2, "fee": 0.2, "hold_bars": 3, "exit_reason": "tp"},
                {"net_pnl": -0.5, "gross_pnl": -0.4, "fee": 0.1, "hold_bars": 1, "exit_reason": "sl"},
            ]
        )
        self.assertEqual(summary["closes"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["win_rate_pct"], 50.0)
        self.assertEqual(summary["net_pnl"], 0.5)
        self.assertEqual(summary["worst"], -0.5)
        self.assertEqual(summary["exit_reasons"], {"tp": 1, "sl": 1})


if __name__ == "__main__":
    unittest.main()
