#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import multi_coin_runner_backfill as backfill


def breakout_candles(count: int = 16) -> list[dict[str, str]]:
    candles: list[dict[str, str]] = []
    base = 1_700_300_000
    for idx in range(count):
        high = 100.0 + min(idx, count - 2)
        if idx == count - 1:
            high = 200.0
        candles.append(
            {
                "start": str(base + idx * 300),
                "open": str(99.0 + idx * 0.1),
                "high": str(high),
                "low": str(98.5 + idx * 0.1),
                "close": str(99.5 + idx * 0.1),
            }
        )
    return candles


class MultiCoinRunnerBackfillTests(unittest.TestCase):
    def test_coin_configs_follow_live_runner_source(self) -> None:
        configs = {row["coin"]: row for row in backfill.COIN_CONFIGS}

        self.assertEqual(configs["NOM-USD"]["strategy"], "range_breakout")
        self.assertEqual(configs["SUP-USD"]["strategy"], "range_breakout")
        self.assertEqual(configs["BAL-USD"]["strategy"], "range_breakout")
        self.assertEqual(configs["CFG-USD"]["strategy"], "momentum")

    def test_range_breakout_signal_is_supported(self) -> None:
        candles = breakout_candles()
        self.assertTrue(backfill.range_breakout_signal(candles[:-1], float(candles[-1]["high"]), 10))


if __name__ == "__main__":
    unittest.main()
