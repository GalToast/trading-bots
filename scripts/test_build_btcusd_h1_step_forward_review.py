#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btcusd_h1_step_forward_review as review


class BTCUSDH1StepForwardReviewTests(unittest.TestCase):
    def test_build_rows_uses_stale_tick_repair_reset_as_forward_baseline(self) -> None:
        rows = review.build_rows(
            [
                {
                    "lane_id": "shadow_btcusd_h1_step30",
                    "realized_usd": "4.0",
                    "closes": "12",
                    "open_count": "1",
                    "floating_usd": "-2.0",
                    "net_usd": "2.0",
                    "updated_at": "2026-04-13T04:00:00+00:00",
                }
            ],
            {
                "shadow_btcusd_h1_step30": {
                    "realized_net_usd": 1.5,
                    "closes": 3,
                    "seeded_at": "2026-04-12T03:00:00+00:00",
                }
            },
            reset_baselines={
                "shadow_btcusd_h1_step30": {
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

    def test_live_row_is_reference_not_candidate(self) -> None:
        rows = review.build_rows(
            [
                {
                    "lane_id": "live_btcusd_exc2_tight_941779",
                    "realized_usd": "231.26",
                    "closes": "36",
                    "open_count": "15",
                    "floating_usd": "-900.0",
                    "net_usd": "-668.74",
                    "updated_at": "2026-04-13T04:00:00+00:00",
                }
            ],
            {},
            reset_baselines={},
        )
        self.assertEqual(rows[0]["forward_status"], "live_reference")
        self.assertEqual(rows[0]["role"], "live_baseline")
        self.assertEqual(rows[0]["realized_delta_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
