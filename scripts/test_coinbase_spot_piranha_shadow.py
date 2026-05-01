#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
import json


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_coinbase_spot_piranha_shadow import CoinbaseSpotPiranhaEngine


class CoinbaseSpotPiranhaTests(unittest.TestCase):
    def test_engine_buys_and_closes_profitable_lot(self) -> None:
        engine = CoinbaseSpotPiranhaEngine(
            product_id="XRP-USD",
            timeframe_name="M1",
            buy_step_px=0.01,
            profit_target_px=0.02,
            quote_per_buy_usd=5.0,
            starting_cash_usd=48.0,
            max_lots=4,
            taker_fee_bps=0.0,
        )
        ticks = [
            {"time": 1, "time_msc": 1000, "bid": 1.00, "ask": 1.01},
            {"time": 2, "time_msc": 2000, "bid": 0.98, "ask": 0.99},
            {"time": 3, "time_msc": 3000, "bid": 1.03, "ask": 1.04},
        ]
        for tick in ticks:
            engine.process_tick(tick, event_path=None, emit=False)
        self.assertGreaterEqual(engine.realized_closes, 1)
        self.assertGreater(engine.cash_usd, 48.0)

    def test_close_event_logs_fee_components(self) -> None:
        engine = CoinbaseSpotPiranhaEngine(
            product_id="XRP-USD",
            timeframe_name="M1",
            buy_step_px=0.01,
            profit_target_px=0.02,
            quote_per_buy_usd=5.0,
            starting_cash_usd=48.0,
            max_lots=4,
            taker_fee_bps=60.0,
        )
        ticks = [
            {"time": 1, "time_msc": 1000, "bid": 1.00, "ask": 1.01},
            {"time": 2, "time_msc": 2000, "bid": 0.98, "ask": 0.99},
            {"time": 3, "time_msc": 3000, "bid": 1.03, "ask": 1.04},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            for tick in ticks:
                engine.process_tick(tick, event_path=event_path, emit=True)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        close_events = [event for event in events if event["action"] == "close_lot"]
        self.assertTrue(close_events)
        close = close_events[0]
        self.assertIn("entry_fee", close)
        self.assertIn("exit_fee", close)
        self.assertIn("fee", close)
        self.assertEqual(close["fee_bps_per_side"], 60.0)


if __name__ == "__main__":
    unittest.main()
