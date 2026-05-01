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

import build_kraken_maker_velocity_bottleneck_review as review


class KrakenMakerVelocityBottleneckReviewTests(unittest.TestCase):
    def test_pairs_guarded_trade_and_gate_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            board_path = root / "board.json"
            events = [
                {
                    "action": "open_maker_shadow",
                    "product_id": "HOT-USD",
                    "ts_utc": "2026-04-25T00:00:00+00:00",
                    "mode": "systemic",
                    "mer": 4.0,
                    "board_spread_bps": 120.0,
                    "live_spread_bps": 110.0,
                    "quote_usd": 8.0,
                },
                {
                    "action": "close_maker_shadow",
                    "product_id": "HOT-USD",
                    "ts_utc": "2026-04-25T00:01:00+00:00",
                    "net": 0.08,
                    "net_pct": 1.0,
                    "age_seconds": 60.0,
                    "reason": "maker_rent_harvest",
                    "cost_usd": 8.0,
                },
            ]
            board = {
                "rows": [
                    {"product_id": "HOT-USD", "playbook": "maker_harvest", "spread_bps": 120.0, "mer": 4.0},
                    {"product_id": "MID-USD", "playbook": "maker_harvest", "spread_bps": 80.0, "mer": 3.0},
                    {"product_id": "BMB-USD", "playbook": "maker_harvest", "spread_bps": 520.0, "mer": 0.5},
                ]
            }
            events_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            board_path.write_text(json.dumps(board), encoding="utf-8")

            payload = review.build_payload(events_path=events_path, board_path=board_path)

            guarded = payload["summary"]["live_spread_guarded"]
            self.assertEqual(guarded["trades"], 1)
            self.assertEqual(guarded["wins"], 1)
            self.assertAlmostEqual(guarded["net_usd"], 0.08)
            self.assertEqual(payload["summary"]["current_gate_counts"]["tight_spread100_mer3p5"]["count"], 1)
            self.assertEqual(payload["summary"]["current_gate_counts"]["middle_spread75_mer2p5"]["count"], 2)
            self.assertEqual(payload["summary"]["current_gate_counts"]["spread_only300_low_mer"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
