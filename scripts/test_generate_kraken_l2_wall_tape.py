#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_kraken_l2_wall_tape as l2


class KrakenL2WallTapeTests(unittest.TestCase):
    def test_book_metrics_computes_l10_imbalance_and_changes(self) -> None:
        payload = {
            "XX": {
                "bids": [["10", "2"], ["9", "1"]],
                "asks": [["11", "1"], ["12", "1"]],
            }
        }

        row = l2.book_metrics(product_id="T-USD", rest_pair="TUSD", depth_payload=payload)

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["l10_bid_usd"], 29.0)
        self.assertEqual(row["l10_ask_usd"], 23.0)
        self.assertAlmostEqual(row["l10_imbalance_ratio"], 29.0 / 23.0, places=6)
        self.assertFalse(row["book_changed"])

        changed = l2.book_metrics(product_id="T-USD", rest_pair="TUSD", depth_payload=payload, previous=row)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertFalse(changed["book_changed"])

    def test_summarize_rows_ranks_bid_wall_leader(self) -> None:
        rows = [
            {"product_id": "A-USD", "l10_imbalance_ratio": 1.0, "l10_obi": 0.5, "spread_bps": 20, "book_changed": True, "bid_change_bps": 1, "ask_change_bps": 1},
            {"product_id": "B-USD", "l10_imbalance_ratio": 4.0, "l10_obi": 0.8, "spread_bps": 30, "book_changed": False, "bid_change_bps": 2, "ask_change_bps": 2},
        ]

        summary = l2.summarize_rows(rows, rolling_window=5)

        self.assertEqual(summary["leaders"][0]["product_id"], "B-USD")
        self.assertEqual(summary["records"], 2)


if __name__ == "__main__":
    unittest.main()
