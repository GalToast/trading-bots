#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_spot_microstructure_sync import analyze_follow_behavior, extract_series


class AnalyzeSpotMicrostructureSyncTests(unittest.TestCase):
    def test_extract_series_ignores_incomplete_rows(self) -> None:
        rows = [
            {"ts_epoch": 1.0, "kraken": {"BTC-USD": {"mid": 100.0}}, "coinbase": {"BTC-USD": {"mid": 99.0}}},
            {"ts_epoch": 2.0, "kraken": {}, "coinbase": {"BTC-USD": {"mid": 100.0}}},
        ]
        series = extract_series(rows, "BTC-USD", "BTC-USD")
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["kraken_mid"], 100.0)

    def test_analyze_follow_behavior_counts_follow_hits(self) -> None:
        series = [
            {"ts_epoch": 1.0, "kraken_mid": 100.0, "coinbase_mid": 100.0},
            {"ts_epoch": 2.0, "kraken_mid": 102.0, "coinbase_mid": 100.0},
            {"ts_epoch": 3.0, "kraken_mid": 102.5, "coinbase_mid": 101.0},
            {"ts_epoch": 4.0, "kraken_mid": 103.0, "coinbase_mid": 101.5},
        ]
        analysis = analyze_follow_behavior(series, move_threshold_usd=1.0, max_follow_samples=2)
        self.assertEqual(analysis["significant_kraken_moves"], 1)
        self.assertEqual(analysis["follow_hits"][1], 1)
        self.assertEqual(analysis["best_follow_window_samples"], 1)


if __name__ == "__main__":
    unittest.main()
