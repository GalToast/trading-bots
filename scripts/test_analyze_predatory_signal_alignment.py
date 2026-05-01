#!/usr/bin/env python3
from __future__ import annotations

import unittest

from analyze_predatory_signal_alignment import align_events


class AnalyzePredatorySignalAlignmentTests(unittest.TestCase):
    def test_align_events_tracks_direction_match(self) -> None:
        events = [
            {
                "ts_utc": "2026-04-12T00:00:02+00:00",
                "action": "iceberg_buy_reload_detected",
                "product_id": "RAVE-USD",
            },
            {
                "ts_utc": "2026-04-12T00:00:03+00:00",
                "action": "iceberg_sell_reload_detected",
                "product_id": "BAL-USD",
            },
        ]
        sync_rows = [
            {
                "ts_utc": "2026-04-12T00:00:01+00:00",
                "coinbase": {
                    "BTC-USD": {"mid": 70000.0},
                    "RAVE-USD": {"mid": 2.0},
                    "BAL-USD": {"mid": 1.0},
                },
            },
            {
                "ts_utc": "2026-04-12T00:00:05+00:00",
                "coinbase": {
                    "BTC-USD": {"mid": 70005.0},
                    "RAVE-USD": {"mid": 2.02},
                    "BAL-USD": {"mid": 0.99},
                },
            },
        ]
        payload = align_events(events, sync_rows, follow_seconds=8.0)
        self.assertEqual(payload["aligned_event_rows"], 2)
        self.assertEqual(payload["by_action"]["iceberg_buy_reload_detected"]["matches"], 1)
        self.assertEqual(payload["by_action"]["iceberg_sell_reload_detected"]["matches"], 1)

    def test_align_events_skips_untracked_products(self) -> None:
        events = [
            {
                "ts_utc": "2026-04-12T00:00:02+00:00",
                "action": "fake_floor_pull_detected",
                "product_id": "ALEPH-USD",
            }
        ]
        sync_rows = [
            {
                "ts_utc": "2026-04-12T00:00:01+00:00",
                "coinbase": {
                    "BTC-USD": {"mid": 70000.0},
                    "RAVE-USD": {"mid": 2.0},
                },
            }
        ]
        payload = align_events(events, sync_rows, follow_seconds=8.0)
        self.assertEqual(payload["aligned_event_rows"], 0)
        self.assertEqual(payload["skipped_untracked_product_rows"], 1)


if __name__ == "__main__":
    unittest.main()
