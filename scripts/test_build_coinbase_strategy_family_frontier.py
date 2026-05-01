#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_strategy_family_frontier as frontier


class CoinbaseStrategyFamilyFrontierTests(unittest.TestCase):
    def test_build_payload_from_rows_summarizes_family_and_coin_winners(self) -> None:
        rows = [
            {"coin": "RAVE-USD", "family": "momentum", "strategy_id": "momentum_lb10", "source": "cache", "net_pnl": 20.0, "trades": 10, "win_rate": 60.0, "max_drawdown": 12.0, "signals": 15, "total_fees": 2.0, "verdict": "positive"},
            {"coin": "RAVE-USD", "family": "vwap_reversion", "strategy_id": "vwap_reversion_default", "source": "cache", "net_pnl": 5.0, "trades": 8, "win_rate": 55.0, "max_drawdown": 10.0, "signals": 9, "total_fees": 1.2, "verdict": "positive"},
            {"coin": "TRU-USD", "family": "momentum", "strategy_id": "momentum_lb10", "source": "cache", "net_pnl": -3.0, "trades": 7, "win_rate": 42.0, "max_drawdown": 22.0, "signals": 12, "total_fees": 1.5, "verdict": "negative"},
            {"coin": "TRU-USD", "family": "vwap_reversion", "strategy_id": "vwap_reversion_default", "source": "cache", "net_pnl": 7.5, "trades": 6, "win_rate": 50.0, "max_drawdown": 8.0, "signals": 7, "total_fees": 1.1, "verdict": "positive"},
            {"coin": "GHST-USD", "family": "momentum", "strategy_id": "momentum_lb10", "source": "cache", "net_pnl": 1.0, "trades": 5, "win_rate": 40.0, "max_drawdown": 18.0, "signals": 9, "total_fees": 0.9, "verdict": "positive"},
            {"coin": "GHST-USD", "family": "vwap_reversion", "strategy_id": "vwap_reversion_default", "source": "cache", "net_pnl": -2.0, "trades": 4, "win_rate": 25.0, "max_drawdown": 14.0, "signals": 5, "total_fees": 0.8, "verdict": "negative"},
            {"coin": "MDT-USD", "family": "missing", "strategy_id": "missing", "source": "missing", "verdict": "missing_candles"},
        ]

        payload = frontier.build_payload_from_rows(rows, now=datetime(2026, 4, 12, 18, 15, 0, tzinfo=timezone.utc))

        self.assertEqual(payload["generated_at"], "2026-04-12T18:15:00+00:00")
        self.assertEqual(payload["missing_coins"], ["MDT-USD"])
        self.assertEqual(payload["family_rows"][0]["family"], "momentum")
        self.assertEqual(payload["family_rows"][1]["family"], "vwap_reversion")
        self.assertEqual(payload["coin_rows"][0]["coin"], "RAVE-USD")
        self.assertEqual(payload["coin_rows"][1]["best_family"], "vwap_reversion")
        self.assertTrue(any("vwap_reversion" in line for line in payload["leadership_read"]))


if __name__ == "__main__":
    unittest.main()
