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

import build_coinbase_spot_mog_rsi_review as review


class CoinbaseSpotMogRSIReviewTests(unittest.TestCase):
    def test_post_reset_close_reports_fee_survival(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_path = root / "state.json"
            event_path = root / "events.jsonl"
            state_path.write_text(
                json.dumps(
                    {
                        "runner": {"lane_name": "mog", "pid": 123, "heartbeat_at": "2026-04-24T00:05:00+00:00"},
                        "state": {
                            "product_id": "MOG-USD",
                            "cash_usd": 2.49,
                            "realized_net_usd": 1.8796,
                            "realized_closes": 1,
                            "in_position": True,
                            "signals_generated": 7,
                            "total_fees": 2.2397,
                            "current_bar": 56,
                            "current_trade": {"entry_price": 0.00000015, "entry_fee": 0.5686, "quantity": 312113560, "entry_bar": 33},
                            "config": {"fee_bps_per_side": 120, "max_hold_bars": 24, "fill_model": "candle_close_proxy"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            events = [
                {"action": "fresh_start_fee_model_reset", "ts_utc": "2026-04-24T00:00:00+00:00", "fee_bps_per_side": 120},
                {
                    "action": "close_trade",
                    "ts_utc": "2026-04-24T01:00:00+00:00",
                    "entry_price": 0.00000015,
                    "exit_price": 0.00000016,
                    "entry_fee": 0.5472,
                    "exit_fee": 0.5767,
                    "fee": 1.1239,
                    "fee_bps_per_side": 120,
                    "gross_pnl": 3.0035,
                    "net_pnl": 1.8796,
                    "hold_bars": 24,
                    "exit_reason": "timeout",
                },
            ]
            event_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

            payload = review.build_review(state_path, event_path)

        self.assertEqual(payload["summary"]["post_reset_wins"], 1)
        self.assertAlmostEqual(payload["latest_close"]["gross_move_pct"], 6.6667, places=3)
        self.assertGreater(payload["latest_close"]["net_pct"], 4.0)
        self.assertEqual(payload["current_open"]["bars_held"], 23)


if __name__ == "__main__":
    unittest.main()
