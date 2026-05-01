#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import benchmark_coinbase_spot_flush_reclaim as benchmark


class CoinbaseSpotFlushReclaimTests(unittest.TestCase):
    def test_summarize_events_reports_positive_and_negative_mix(self) -> None:
        events = [
            SimpleNamespace(net_return_pct=0.02, bars_held=2),
            SimpleNamespace(net_return_pct=-0.01, bars_held=1),
            SimpleNamespace(net_return_pct=0.03, bars_held=3),
        ]
        summary = benchmark.summarize_events(events)
        self.assertEqual(summary["signals"], 3)
        self.assertEqual(summary["wins"], 2)
        self.assertEqual(summary["losses"], 1)
        self.assertAlmostEqual(summary["cumulative_net_pct"], 4.0)

    def test_build_rows_sorts_by_cumulative_net(self) -> None:
        market = {"AAA-USD": [object()], "BBB-USD": [object()]}

        original = benchmark._simulate_product_tactic
        try:
            def fake_simulate(candles, *, signal_name, fee_bps_per_side):
                if candles is market["AAA-USD"]:
                    return [SimpleNamespace(net_return_pct=0.01, bars_held=1)]
                return [SimpleNamespace(net_return_pct=-0.01, bars_held=1)]

            benchmark._simulate_product_tactic = fake_simulate
            rows = benchmark.build_rows(
                market=market,
                tactic="flush_reclaim",
                fee_bps_per_side=40.0,
                product_meta={"AAA-USD": {"pct24h": 5.0, "volume_24h": 1000.0}},
            )
        finally:
            benchmark._simulate_product_tactic = original

        self.assertEqual(rows[0]["product_id"], "AAA-USD")
        self.assertEqual(rows[1]["product_id"], "BBB-USD")
        self.assertEqual(rows[0]["cumulative_net_pct"], 1.0)
        self.assertEqual(rows[1]["cumulative_net_pct"], -1.0)


if __name__ == "__main__":
    unittest.main()
