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

import multi_coin_isolated_runner as runner


def candle(ts: int, open_price: float, high: float, low: float, close: float) -> dict[str, str]:
    return {
        "start": str(ts),
        "open": f"{open_price:.6f}",
        "high": f"{high:.6f}",
        "low": f"{low:.6f}",
        "close": f"{close:.6f}",
    }


class MultiCoinIsolatedRunnerTests(unittest.TestCase):
    def test_runner_source_avoids_non_ascii_console_markers(self) -> None:
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("🎯", source)

    def test_load_runner_configs_supports_configs_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runner_config.json"
            path.write_text(
                json.dumps(
                    {
                        "configs": [
                            {
                                "coin": "TEST-USD",
                                "strategy": "momentum",
                                "lookback": 25,
                                "tp_pct": 0.1,
                                "sl_pct": 0.03,
                                "max_hold": 48,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            configs = runner.load_runner_configs(str(path))

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["coin"], "TEST-USD")
        self.assertEqual(configs[0]["lookback"], 25)

    def test_range_breakout_strategy_can_open_position(self) -> None:
        cfg = {
            "coin": "TEST-USD",
            "strategy": "range_breakout",
            "range_lookback": 3,
            "tp_pct": 0.10,
            "sl_pct": 0.03,
            "max_hold": 24,
        }
        ledger = runner.CoinLedger(cfg, starting_cash=48.0)
        base_ts = 1776002400
        candles = [
            candle(base_ts + 0 * 300, 1.00, 1.01, 0.99, 1.00),
            candle(base_ts + 1 * 300, 1.00, 1.02, 0.99, 1.01),
            candle(base_ts + 2 * 300, 1.01, 1.03, 1.00, 1.02),
            candle(base_ts + 3 * 300, 1.02, 1.04, 1.01, 1.03),
            candle(base_ts + 4 * 300, 1.03, 1.20, 1.02, 1.05),
        ]

        events = ledger.process_candles(candles, backfill=False)

        self.assertEqual(events[-1]["action"], "open")
        self.assertEqual(events[-1]["strategy"], "range_breakout")
        self.assertIsNotNone(ledger.position)


if __name__ == "__main__":
    unittest.main()
