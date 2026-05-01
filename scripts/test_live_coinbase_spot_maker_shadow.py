#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_coinbase_spot_maker_shadow as runner


class CoinbaseMakerShadowTests(unittest.TestCase):
    def test_parse_min_notional_overrides(self) -> None:
        self.assertEqual(
            runner.parse_min_notional_overrides("SPX-USD=1,FLOCK-USD=2.5"),
            {"SPX-USD": 1.0, "FLOCK-USD": 2.5},
        )
        with self.assertRaises(ValueError):
            runner.parse_min_notional_overrides("SPX-USD")
        with self.assertRaises(ValueError):
            runner.parse_min_notional_overrides("SPX-USD=0")

    def test_entry_veto_logs_product_min_notional_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            state = runner.ShadowState(["SPX-USD"], max_quote_usd=8.0)
            engine = runner.ShadowEngine(state, min_notionals={"SPX-USD": 10.0})

            pos = engine.try_enter("SPX-USD", bid=1.0, ask=1.1, spread_bps=950.0, event_path=event_path)

            self.assertIsNone(pos)
            self.assertEqual(state.positions, [])
            event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["action"], "coinbase_entry_veto")
            self.assertEqual(event["reason"], "quote_below_min_notional")
            self.assertEqual(event["quote_usd"], 8.0)
            self.assertEqual(event["min_notional_usd"], 10.0)

    def test_entry_uses_override_when_quote_clears_min_notional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            state = runner.ShadowState(["SPX-USD"], max_quote_usd=8.0)
            engine = runner.ShadowEngine(state, min_notionals={"SPX-USD": 1.0})
            original_random = random.random
            random.random = lambda: 0.5
            try:
                pos = engine.try_enter(
                    "SPX-USD",
                    bid=1.0,
                    ask=1.1,
                    spread_bps=950.0,
                    event_path=event_path,
                    bid_depth_usd=100.0,
                )
            finally:
                random.random = original_random

            self.assertIsNotNone(pos)
            self.assertEqual(len(state.positions), 1)
            self.assertAlmostEqual(state.positions[0]["cost_usd"], 8.048)


if __name__ == "__main__":
    unittest.main()
