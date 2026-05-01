#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_maker_post_close_ghost_review as review


class KrakenMakerPostCloseGhostReviewTests(unittest.TestCase):
    def test_summarizes_horizon_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [
                {
                    "action": "post_close_ghost_mark",
                    "product_id": "A-USD",
                    "horizon_seconds": 30,
                    "close_reason": "maker_min_profit_harvest",
                    "delta_net_vs_actual": 0.10,
                    "delta_net_pct_vs_actual": 1.0,
                },
                {
                    "action": "post_close_ghost_mark",
                    "product_id": "B-USD",
                    "horizon_seconds": 30,
                    "close_reason": "maker_min_profit_harvest",
                    "delta_net_vs_actual": -0.02,
                    "delta_net_pct_vs_actual": -0.2,
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            payload = review.build_payload(path)

            self.assertEqual(payload["summary"]["ghost_marks"], 2)
            self.assertEqual(payload["summary"]["improved_marks"], 1)
            self.assertEqual(payload["summary"]["worsened_marks"], 1)
            self.assertAlmostEqual(payload["summary"]["avg_delta_net"], 0.04)
            self.assertEqual(payload["by_horizon"][0]["horizon_seconds"], 30)
            self.assertAlmostEqual(payload["by_horizon"][0]["best_delta_net"], 0.10)


if __name__ == "__main__":
    unittest.main()
