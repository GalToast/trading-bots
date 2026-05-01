#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_volatility_frontier as frontier


class CoinbaseVolatilityFrontierTests(unittest.TestCase):
    def test_build_payload_from_rows_marks_frontier_crossovers(self) -> None:
        rows = [
            {"coin": "RAVE-USD", "family": "atr_expansion", "strategy_id": "atr_expansion_default", "source": "cache", "net_pnl": 12.0, "trades": 8, "win_rate": 62.0, "max_drawdown": 9.0, "signals": 10, "total_fees": 1.2, "verdict": "positive"},
            {"coin": "RAVE-USD", "family": "keltner_breakout", "strategy_id": "keltner_breakout_default", "source": "cache", "net_pnl": 4.0, "trades": 6, "win_rate": 50.0, "max_drawdown": 12.0, "signals": 8, "total_fees": 0.8, "verdict": "positive"},
            {"coin": "TRU-USD", "family": "atr_expansion", "strategy_id": "atr_expansion_default", "source": "cache", "net_pnl": -2.0, "trades": 5, "win_rate": 40.0, "max_drawdown": 14.0, "signals": 7, "total_fees": 0.7, "verdict": "negative"},
            {"coin": "TRU-USD", "family": "hist_vol_squeeze", "strategy_id": "hist_vol_squeeze_default", "source": "cache", "net_pnl": -1.0, "trades": 4, "win_rate": 25.0, "max_drawdown": 10.0, "signals": 4, "total_fees": 0.4, "verdict": "negative"},
            {"coin": "MDT-USD", "family": "missing", "strategy_id": "missing", "source": "missing", "verdict": "missing_candles"},
        ]
        family_frontier_payload = {
            "coin_rows": [
                {"coin": "RAVE-USD", "best_family": "momentum", "best_net_pnl": 10.0},
                {"coin": "TRU-USD", "best_family": "range_breakout", "best_net_pnl": 3.0},
            ]
        }

        payload = frontier.build_payload_from_rows(
            rows,
            family_frontier_payload=family_frontier_payload,
            now=datetime(2026, 4, 12, 18, 40, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["generated_at"], "2026-04-12T18:40:00+00:00")
        self.assertEqual(payload["missing_coins"], ["MDT-USD"])
        self.assertEqual(payload["family_rows"][0]["family"], "atr_expansion")
        self.assertEqual(payload["coin_rows"][0]["coin"], "RAVE-USD")
        self.assertTrue(payload["coin_rows"][0]["beats_family_frontier"])
        self.assertFalse(payload["coin_rows"][1]["beats_family_frontier"])
        self.assertTrue(any("RAVE" in line for line in payload["leadership_read"]))


if __name__ == "__main__":
    unittest.main()
