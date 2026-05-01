#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_l2_imbalance_forward_replay as replay


class KrakenL2ImbalanceForwardReplayTests(unittest.TestCase):
    def test_label_rows_marks_fee_clear_horizon(self) -> None:
        events = [
            {
                "product_id": "A-USD",
                "ts_epoch": 0.0,
                "ts_utc": "t0",
                "bid": 99.0,
                "ask": 100.0,
                "spread_bps": 100.0,
                "l10_imbalance_ratio": 2.0,
                "l10_obi": 0.66,
                "l10_bid_usd": 200.0,
                "l10_ask_usd": 100.0,
            },
            {
                "product_id": "A-USD",
                "ts_epoch": 10.0,
                "ts_utc": "t1",
                "bid": 101.5,
                "ask": 102.0,
                "spread_bps": 49.0,
                "l10_imbalance_ratio": 1.0,
                "l10_obi": 0.5,
                "l10_bid_usd": 100.0,
                "l10_ask_usd": 100.0,
            },
        ]

        rows = replay.label_rows(events, horizon_seconds=10.0, fee_bps=100.0, min_net_bps=0.0)

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["fee_clear_horizon"])
        self.assertAlmostEqual(rows[0]["horizon_taker_net_bps"], 50.0, places=6)

    def test_summarize_groups_product_and_bucket(self) -> None:
        labels = [
            {"product_id": "A-USD", "spread_bps": 20.0, "l10_imbalance_ratio": 2.2, "horizon_taker_net_bps": 1.0, "mfe_taker_net_bps": 2.0, "fee_clear_horizon": True, "fee_clear_mfe": True},
            {"product_id": "B-USD", "spread_bps": 120.0, "l10_imbalance_ratio": 0.5, "horizon_taker_net_bps": -1.0, "mfe_taker_net_bps": 0.5, "fee_clear_horizon": False, "fee_clear_mfe": True},
        ]

        summary = replay.summarize(labels)

        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["products"][0]["product_id"], "A-USD")
        self.assertEqual(summary["products"][0]["fee_clear_horizon_rate"], 1.0)
        self.assertEqual(len(summary["buckets"]), 2)


if __name__ == "__main__":
    unittest.main()
