#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_rsi_forward_review as review


class CoinbaseSpotRSIForwardReviewTests(unittest.TestCase):
    def test_classify_forward_row_respects_sample_size_before_sign(self) -> None:
        row = {
            "product_id": "ARB-USD",
            "realized_closes": "2",
            "realized_net_usd": "0.4449",
            "in_position": "0",
        }
        status, note = review.classify_forward_row(row)
        self.assertEqual(status, "bootstrap_positive")
        self.assertIn("too few closes", note)

    def test_build_rows_flags_negative_mature_lane(self) -> None:
        rows = review.build_rows(
            [
                {
                    "product_id": "PRL-USD",
                    "lane_name": "shadow_coinbase_prlusd_rsi7",
                    "readiness_verdict": "probationary",
                    "baseline_72h_net_usd": "2.2555",
                    "realized_net_usd": "-0.3157",
                    "realized_closes": "5",
                    "in_position": "1",
                    "cash_usd": "4.76",
                    "heartbeat_age_seconds": "19.9",
                },
                {
                    "product_id": "TOTAL",
                    "lane_name": "TOTAL",
                    "readiness_verdict": "supervised_pack",
                    "baseline_72h_net_usd": "24.1828",
                    "realized_net_usd": "2.5685",
                    "realized_closes": "26",
                    "in_position": "3",
                    "cash_usd": "160.75",
                    "heartbeat_age_seconds": "",
                    "note": "lanes=6",
                },
            ]
        )
        self.assertEqual(rows[0]["forward_status"], "lagging_in_position")
        self.assertEqual(rows[-1]["forward_status"], "pack_total")


if __name__ == "__main__":
    unittest.main()
