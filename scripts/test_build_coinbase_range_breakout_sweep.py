#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_range_breakout_sweep as sweep


class CoinbaseRangeBreakoutSweepTests(unittest.TestCase):
    def test_build_leadership_read_and_payload_order(self) -> None:
        coin_rows = [
            {
                "coin": "NOM-USD",
                "best_net_pnl": 700.0,
                "best_range_lookback": 30,
                "best_tp_pct": 8.0,
                "best_sl_pct": 1.0,
                "best_max_hold": 24,
                "uplift_vs_default_momentum": 150.0,
                "profitable_rate": 62.5,
                "uplift_vs_default_breakout": 120.0,
            },
            {
                "coin": "SUP-USD",
                "best_net_pnl": 35.0,
                "best_range_lookback": 12,
                "best_tp_pct": 5.0,
                "best_sl_pct": 1.0,
                "best_max_hold": 12,
                "uplift_vs_default_momentum": 4.0,
                "profitable_rate": 41.0,
                "uplift_vs_default_breakout": 8.0,
            },
            {
                "coin": "BAL-USD",
                "best_net_pnl": 14.0,
                "best_range_lookback": 20,
                "best_tp_pct": 3.0,
                "best_sl_pct": 0.0,
                "best_max_hold": 24,
                "uplift_vs_default_momentum": -1.0,
                "profitable_rate": 18.0,
                "uplift_vs_default_breakout": 2.0,
            },
        ]

        lines = sweep.build_leadership_read(coin_rows)
        self.assertTrue(any("NOM" in line and "headline breakout-continuation lane" in line for line in lines))
        self.assertTrue(any("NOM and SUP" in line and "beat the shared momentum baseline" in line for line in lines))
        self.assertTrue(any("BAL" in line and "remain thin" in line for line in lines))

    def test_load_target_coins_prefers_positive_range_breakout_winners(self) -> None:
        payload = {
            "coin_rows": [
                {"coin": "CFG-USD", "best_family": "range_breakout", "best_net_pnl": 11.39},
                {"coin": "NOM-USD", "best_family": "range_breakout", "best_net_pnl": 512.39},
                {"coin": "SUP-USD", "best_family": "range_breakout", "best_net_pnl": 35.69},
                {"coin": "BAL-USD", "best_family": "range_breakout", "best_net_pnl": 12.43},
                {"coin": "PRL-USD", "best_family": "range_breakout", "best_net_pnl": 27.05},
                {"coin": "XRP-USD", "best_family": "vwap_reversion", "best_net_pnl": -2.76},
            ]
        }
        old_load_json = sweep.load_json
        try:
            sweep.load_json = lambda path: payload
            coins = sweep.load_target_coins(limit=4)
        finally:
            sweep.load_json = old_load_json

        self.assertEqual(coins, ["NOM-USD", "SUP-USD", "PRL-USD", "BAL-USD"])


if __name__ == "__main__":
    unittest.main()
