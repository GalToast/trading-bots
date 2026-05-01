#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import coinbase_opportunity_sweep as sweep


def make_candle(ts: int, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "start": ts,
        "open": str(open_),
        "high": str(high),
        "low": str(low),
        "close": str(close),
    }


class CoinbaseOpportunitySweepTests(unittest.TestCase):
    def test_run_strategy_backtest_reports_strategy_library_engine(self) -> None:
        candles = [
            make_candle(1776003900, 9.0, 10.0, 8.5, 9.5),
            make_candle(1776004200, 9.5, 11.0, 9.4, 10.0),
            make_candle(1776004500, 10.0, 12.0, 9.9, 11.0),
            make_candle(1776004800, 11.0, 11.1, 10.8, 11.0),
        ]
        strategy = {"name": "mom_2", "type": "momentum", "params": {"lookback": 2, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 10}}

        result = sweep.run_strategy_backtest(candles, strategy)

        self.assertEqual(result["engine"], "strategy_library")
        self.assertEqual(result["closes"], 1)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["net_pnl"], 4.38)


if __name__ == "__main__":
    unittest.main()
