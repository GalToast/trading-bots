#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest

from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_burst_forward_review as review


class CoinbaseBurstForwardReviewTests(unittest.TestCase):
    def test_classify_forward_row_seeds_until_enough_new_closes(self) -> None:
        row = {
            "lane_name": "shadow_coinbase_burst_multicoin_rotation",
            "realized_net_usd": "90.0",
            "closes": "490",
            "open_count": "0",
        }
        baseline = {"realized_net_usd": 89.0, "closes": 488}
        status, note, delta_realized, delta_closes = review.classify_forward_row(row, baseline)
        self.assertEqual(status, "seeded_positive")
        self.assertIn("too few new closes", note)
        self.assertEqual(delta_closes, 2)

    def test_build_rows_flags_negative_mature_burst_lane(self) -> None:
        rows = review.build_rows(
            [
                {
                    "lane_name": "shadow_coinbase_burst_roundrobin_best",
                    "style": "roundrobin_best",
                    "realized_net_usd": "670.0",
                    "closes": "190",
                    "open_count": "1",
                    "cash_usd": "40.0",
                    "heartbeat_age_seconds": "10.0",
                },
                {
                    "lane_name": "TOTAL",
                    "style": "supervised_burst_pack",
                    "realized_net_usd": "1670.0",
                    "closes": "1000",
                    "open_count": "1",
                    "cash_usd": "900.0",
                    "heartbeat_age_seconds": "",
                    "note": "lanes=4",
                },
            ],
            {
                "shadow_coinbase_burst_roundrobin_best": {"realized_net_usd": 680.0, "closes": 184},
            },
        )
        self.assertEqual(rows[0]["forward_status"], "lagging_in_position")
        self.assertEqual(rows[-1]["forward_status"], "pack_total")


if __name__ == "__main__":
    unittest.main()
