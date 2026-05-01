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

from coinbase_advanced_client import CoinbaseAdvancedClientError
from live_coinbase_rsi_shadow import CoinbaseRSIShadowEngine, RSITrade, apply_latest_candle, fetch_latest_candle, restore_engine_from_payload


class AlwaysRateLimitedClient:
    def market_candles(self, *args, **kwargs):
        raise CoinbaseAdvancedClientError("HTTP 429 /api/v3/brokerage/market/products/LIGHTER-USD/candles: {}")


class CoinbaseRSIShadowTests(unittest.TestCase):
    def test_missing_latest_candle_is_idle_poll_not_exception(self) -> None:
        engine = CoinbaseRSIShadowEngine(
            product_id="LIGHTER-USD",
            starting_cash_usd=48.0,
            rsi_period=7,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            profit_target_pct=0.02,
            stop_loss_pct=0.003,
            max_hold_bars=48,
            maker_fee_bps=5.0,
            deploy_pct=0.9,
        )
        runner_status = {
            "pid": 123,
            "script": "live_coinbase_rsi_shadow.py",
            "started_at": "2026-04-11T00:00:00Z",
            "poll_seconds": 30.0,
            "heartbeat_at": None,
            "last_successful_run_at": None,
            "consecutive_exceptions": 2,
            "last_exception_at": "2026-04-11T00:01:00Z",
            "last_exception_type": "RuntimeError",
            "last_exception_message": "No candle returned",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            event_path = Path(tmpdir) / "events.jsonl"
            apply_latest_candle(
                engine,
                None,
                runner_status=runner_status,
                state_path=state_path,
                event_path=event_path,
            )
            self.assertTrue(state_path.exists())
            self.assertIsNotNone(runner_status["heartbeat_at"])
            self.assertEqual(runner_status["consecutive_exceptions"], 0)
            self.assertEqual(runner_status["last_exception_type"], "")
            self.assertEqual(engine.current_bar, 0)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"]["product_id"], "LIGHTER-USD")
            self.assertEqual(payload["state"]["current_bar"], 0)

    def test_fetch_latest_candle_returns_none_and_logs_skip_on_429(self) -> None:
        events = []
        latest = fetch_latest_candle(
            AlwaysRateLimitedClient(),
            "LIGHTER-USD",
            "FIVE_MINUTE",
            event_logger=events.append,
        )
        self.assertIsNone(latest)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "rate_limit_skip_live_fetch")
        self.assertEqual(events[0]["product"], "LIGHTER-USD")
        self.assertEqual(events[0]["limit"], 1)

    def test_restore_engine_from_payload_preserves_shadow_state(self) -> None:
        engine = CoinbaseRSIShadowEngine(
            product_id="LIGHTER-USD",
            starting_cash_usd=48.0,
            rsi_period=7,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            profit_target_pct=0.02,
            stop_loss_pct=0.003,
            max_hold_bars=48,
            maker_fee_bps=5.0,
            deploy_pct=0.9,
        )
        payload = {
            "state": {
                "product_id": "LIGHTER-USD",
                "cash_usd": 4.82,
                "realized_net_usd": 0.6061,
                "realized_closes": 17,
                "in_position": True,
                "current_bar": 148,
                "last_candle_time": 1775975700,
                "total_fees": 1.1235,
                "signals_generated": 78,
                "current_trade": {
                    "entry_time": 1775975700,
                    "entry_price": 1.149,
                    "direction": "BUY",
                    "quantity": 37.76611657809776,
                    "entry_rsi": 23.33,
                    "entry_bar": 147,
                    "entry_fee": 0.0217,
                    "exit_time": 0,
                    "exit_price": 0.0,
                    "exit_reason": "",
                    "exit_rsi": 0.0,
                    "gross_pnl": 0.0,
                    "fee": 0.0,
                    "net_pnl": 0.0,
                    "hold_bars": 0,
                },
                "config": {
                    "rsi_period": 7,
                    "oversold_threshold": 30.0,
                    "overbought_threshold": 70.0,
                    "profit_target_pct": 0.02,
                    "stop_loss_pct": 0.003,
                    "max_hold_bars": 48,
                    "maker_fee_bps": 5.0,
                    "fee_model": "coinbase_spot_shadow_fee_bps_per_side",
                    "deploy_pct": 0.9,
                    "candle_granularity": "FIVE_MINUTE",
                },
            }
        }
        bootstrap_candles = [
            {"time": i, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0 + (i * 0.001), "volume": 1.0}
            for i in range(80)
        ]

        restored = restore_engine_from_payload(engine, payload, bootstrap_candles=bootstrap_candles)
        self.assertTrue(restored)
        self.assertEqual(engine.cash_usd, 4.82)
        self.assertEqual(engine.realized_closes, 17)
        self.assertTrue(engine.in_position)
        self.assertIsInstance(engine.current_trade, RSITrade)
        self.assertEqual(engine.current_trade.entry_price, 1.149)
        self.assertEqual(engine.current_bar, 148)
        self.assertEqual(engine.last_candle_time, 1775975700)
        self.assertEqual(len(engine.price_history), engine.rsi_period + 50)

    def test_close_net_pnl_subtracts_entry_and_exit_fees(self) -> None:
        engine = CoinbaseRSIShadowEngine(
            product_id="RAVE-USD",
            starting_cash_usd=100.0,
            rsi_period=7,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            profit_target_pct=0.02,
            stop_loss_pct=0.003,
            max_hold_bars=48,
            maker_fee_bps=60.0,
            deploy_pct=0.9,
        )
        engine.price_history = [1.0] * 20
        engine.in_position = True
        engine.current_bar = 10
        engine.cash_usd = 10.0
        engine.current_trade = RSITrade(
            entry_time=1,
            entry_price=1.0,
            direction="BUY",
            quantity=10.0,
            entry_rsi=20.0,
            entry_bar=9,
            entry_fee=0.06,
        )
        engine.process_candle({"time": 2, "open": 1.0, "high": 1.03, "low": 1.0, "close": 1.02, "volume": 1.0})
        self.assertFalse(engine.in_position)
        self.assertEqual(engine.realized_closes, 1)
        # Gross is 0.20, entry fee is 0.06, exit fee is 10.2 * 0.006 = 0.0612.
        self.assertAlmostEqual(engine.realized_net_usd, 0.0788)
        self.assertAlmostEqual(engine.total_fees, 0.1212)


if __name__ == "__main__":
    unittest.main()
