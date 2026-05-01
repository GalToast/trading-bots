#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_rsi_parallel_shadow as rsi_parallel


class RSIParallelShadowTests(unittest.TestCase):
    def test_new_candles_since_filters_by_last_candle_time(self) -> None:
        raw = [
            {"start": 1710000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"start": 1710000300, "open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 11},
            {"start": 1710000600, "open": 3, "high": 4, "low": 2.5, "close": 3.5, "volume": 12},
        ]

        new = rsi_parallel.new_candles_since(raw, 1710000300)

        self.assertEqual([c["time"] for c in new], [1710000600])

    def test_live_candle_filter_does_not_depend_on_current_bar_count(self) -> None:
        engine = rsi_parallel.RSIParallelEngine(["IOTX-USD"], {"IOTX-USD": {"p": 4, "os": 30, "t": 5, "s": 3, "ob": 70}})
        engine.coin["IOTX-USD"]["current_bar"] = 2
        engine.coin["IOTX-USD"]["last_candle_time"] = 1710000300

        raw = [
            {"start": 1710000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"start": 1710000300, "open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 11},
            {"start": 1710000600, "open": 3, "high": 4, "low": 2.5, "close": 3.5, "volume": 12},
        ]

        new = rsi_parallel.new_candles_since(raw, engine.coin["IOTX-USD"]["last_candle_time"])

        self.assertEqual([c["time"] for c in new], [1710000600])


if __name__ == "__main__":
    unittest.main()
