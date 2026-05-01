#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sweep_coinbase_spot_reclaim_params as sweep


class SweepCoinbaseSpotReclaimParamsTests(unittest.TestCase):
    def test_summarize_events_counts_pnl_correctly(self) -> None:
        events = [
            SimpleNamespace(net_return_pct=0.01, bars_held=1),
            SimpleNamespace(net_return_pct=-0.02, bars_held=2),
            SimpleNamespace(net_return_pct=0.03, bars_held=3),
        ]
        summary = sweep.summarize_events(events)
        self.assertEqual(summary["signals"], 3)
        self.assertEqual(summary["wins"], 2)
        self.assertEqual(summary["losses"], 1)
        self.assertAlmostEqual(summary["cumulative_net_pct"], 2.0)

    def test_run_sweep_surfaces_best_product_rows(self) -> None:
        market = {"AAA-USD": [object()], "BBB-USD": [object()]}
        configs = [
            sweep.ReclaimConfig(0.02, 0.01, 0.7, 0.01, 0.02, 0.04, 8),
            sweep.ReclaimConfig(0.03, 0.02, 0.75, 0.01, 0.03, 0.06, 8),
        ]

        original = sweep.simulate_config
        try:
            def fake_simulate(candles, config, fee_bps_per_side):
                if candles is market["AAA-USD"] and config.flush_threshold_pct == 0.02:
                    return [SimpleNamespace(net_return_pct=0.02, bars_held=1)]
                if candles is market["BBB-USD"] and config.flush_threshold_pct == 0.03:
                    return [SimpleNamespace(net_return_pct=0.01, bars_held=1)]
                return []

            sweep.simulate_config = fake_simulate
            summary_rows, product_rows = sweep.run_sweep(market, configs=configs, fee_bps_per_side=40.0)
        finally:
            sweep.simulate_config = original

        self.assertEqual(summary_rows[0]["positive_products"], 1)
        self.assertEqual(product_rows[0]["product_id"], "AAA-USD")
        self.assertEqual(product_rows[1]["product_id"], "BBB-USD")


if __name__ == "__main__":
    unittest.main()
