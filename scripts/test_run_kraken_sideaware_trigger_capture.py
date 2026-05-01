#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_sideaware_trigger_capture as capture


class KrakenSideawareTriggerCaptureTests(unittest.TestCase):
    def test_directional_summary_ranks_fill_like_side_trials(self) -> None:
        records = [
            {"product_id": "A-USD", "side": "buy", "offset_frac": 0.5, "fill_like": False},
            {"product_id": "B-USD", "side": "buy", "offset_frac": 0.5, "fill_like": True},
            {"product_id": "B-USD", "side": "buy", "offset_frac": 0.5, "fill_like": False},
        ]

        summary = capture.directional_summary(records)

        self.assertEqual(summary["records"], 3)
        self.assertEqual(summary["fill_like_records"], 1)
        self.assertEqual(summary["leaders"][0]["key"], "B-USD|buy|0.5000")
        self.assertEqual(summary["leaders"][0]["fill_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
