#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_vulture_reclaim_replay as replay
from build_kraken_vulture_reversal_replay import Sample


class KrakenVultureReclaimReplayTests(unittest.TestCase):
    def test_find_reclaim_entry_waits_for_bounce_from_low(self) -> None:
        samples = [
            Sample(ts=1.0, bid=100.0, ask=101.0),
            Sample(ts=2.0, bid=95.0, ask=96.0),
            Sample(ts=3.0, bid=90.0, ask=91.0),
            Sample(ts=4.0, bid=91.0, ask=92.0),
            Sample(ts=5.0, bid=93.0, ask=94.0),
            Sample(ts=6.0, bid=94.0, ask=95.0),
        ]

        entry_index, low_bid, reclaim_bps, low_index = replay.find_reclaim_entry(
            samples,
            signal_index=1,
            confirm_bps=200.0,
            entry_timeout_samples=4,
        )

        self.assertEqual(low_index, 2)
        self.assertEqual(entry_index, 5)
        self.assertAlmostEqual(low_bid, 90.0)
        self.assertGreaterEqual(reclaim_bps, 200.0)


if __name__ == "__main__":
    unittest.main()
