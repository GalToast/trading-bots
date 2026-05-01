#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import multi_coin_portfolio as portfolio


def breakout_candles(count: int = 16) -> list[dict[str, str]]:
    candles: list[dict[str, str]] = []
    base = 1_700_100_000
    for idx in range(count):
        high = 100.0 + min(idx, count - 2)
        if idx == count - 1:
            high = 200.0
        candles.append(
            {
                "start": str(base + idx * 300),
                "open": str(99.0 + idx * 0.1),
                "high": str(high),
                "low": str(98.5 + idx * 0.1),
                "close": str(99.5 + idx * 0.1),
            }
        )
    return candles


class MultiCoinPortfolioTests(unittest.TestCase):
    def test_source_configs_align_breakout_family_to_board(self) -> None:
        self.assertEqual(portfolio.STRATEGY_CONFIGS["NOM-USD"]["type"], "range_breakout")
        self.assertEqual(portfolio.STRATEGY_CONFIGS["SUP-USD"]["type"], "range_breakout")
        self.assertEqual(portfolio.STRATEGY_CONFIGS["BAL-USD"]["type"], "range_breakout")
        self.assertEqual(portfolio.STRATEGY_CONFIGS["CFG-USD"]["type"], "momentum")
        self.assertEqual(portfolio.STRATEGY_CONFIGS["NOM-USD"]["range_lookback"], 10)
        self.assertEqual(portfolio.STRATEGY_CONFIGS["SUP-USD"]["range_lookback"], 8)
        self.assertEqual(portfolio.STRATEGY_CONFIGS["BAL-USD"]["range_lookback"], 50)
        self.assertEqual(portfolio.STRATEGY_CONFIGS["CFG-USD"]["lookback"], 50)

    def test_range_breakout_signal_opens_position(self) -> None:
        config = {
            "type": "range_breakout",
            "range_lookback": 10,
            "tp_pct": 10.0,
            "sl_pct": 1.0,
            "max_hold": 24,
            "weight": 1.0,
        }
        engine = portfolio.CoinEngine("NOM-USD", config, starting_cash=48.0)
        candles = breakout_candles()

        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            for candle in candles:
                engine.process_candle(candle, event_path, phase="test")

        self.assertIsNotNone(engine.position)
        self.assertGreaterEqual(engine.signals, 1)
        self.assertEqual(engine.snapshot()["position"], "active")

    def test_default_output_paths_isolate_single_coin_runs(self) -> None:
        state_path, event_path = portfolio.default_output_paths(["NOM-USD"])

        self.assertTrue(str(state_path).endswith("reports\\multi_coin_portfolio_nomusd_state.json"))
        self.assertTrue(str(event_path).endswith("reports\\multi_coin_portfolio_nomusd_events.jsonl"))

    def test_default_output_paths_keep_shared_names_for_multi_coin_runs(self) -> None:
        state_path, event_path = portfolio.default_output_paths(["NOM-USD", "SUP-USD"])

        self.assertEqual(state_path, portfolio.STATE_PATH)
        self.assertEqual(event_path, portfolio.EVENT_PATH)

    def test_default_output_paths_keep_shared_names_for_empty_coin_list(self) -> None:
        state_path, event_path = portfolio.default_output_paths([])

        self.assertEqual(state_path, portfolio.STATE_PATH)
        self.assertEqual(event_path, portfolio.EVENT_PATH)


if __name__ == "__main__":
    unittest.main()
