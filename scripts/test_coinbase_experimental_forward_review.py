#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_experimental_forward_review as review


class CoinbaseExperimentalForwardReviewTests(unittest.TestCase):
    def test_build_rows_uses_stale_tick_repair_reset_as_forward_baseline(self) -> None:
        rows = review.build_rows(
            [
                {
                    "lane_name": "shadow_coinbase_experimental_rotation_bb_rsi",
                    "style": "rotation_bb_rsi",
                    "product_id": "",
                    "realized_net_usd": "4.0",
                    "closes": "12",
                    "open_count": "1",
                    "cash_usd": "40.0",
                    "heartbeat_age_seconds": "8.0",
                }
            ],
            {
                "shadow_coinbase_experimental_rotation_bb_rsi": {
                    "realized_net_usd": 1.5,
                    "closes": 3,
                    "seeded_at": "2026-04-12T03:00:00+00:00",
                }
            },
            reset_baselines={
                "shadow_coinbase_experimental_rotation_bb_rsi": {
                    "realized_net_usd": 3.25,
                    "closes": 10,
                    "reset_at": "2026-04-12T04:00:00+00:00",
                    "reset_type": "stale_tick_repair",
                }
            },
        )

        self.assertEqual(rows[0]["baseline_source"], "stale_tick_repair")
        self.assertEqual(rows[0]["baseline_realized_usd"], 3.25)
        self.assertEqual(rows[0]["baseline_closes"], 10)
        self.assertEqual(rows[0]["realized_delta_usd"], 0.75)
        self.assertEqual(rows[0]["new_closes"], 2)
        self.assertIn("clean forward since stale-tick repair", rows[0]["forward_note"])


if __name__ == "__main__":
    unittest.main()
