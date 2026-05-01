#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_grid_exit_first_router as router
from kraken_spot_client import KrakenPair
from run_kraken_grid_shadow_tape import TradePrint


class KrakenGridExitFirstRouterTests(unittest.TestCase):
    def test_replay_requires_entry_before_exit(self) -> None:
        trades = [
            TradePrint(price=101.0, size=1.0, ts=1.0, side="b", trade_id=1),
            TradePrint(price=100.0, size=1.0, ts=2.0, side="s", trade_id=2),
        ]

        result = router.replay_recent_roundtrip(
            trades,
            buy_price=100.0,
            target_price=101.0,
            allocation_usd=50.0,
            participation=1.0,
        )

        self.assertTrue(result.entry_ok)
        self.assertFalse(result.exit_ok)
        self.assertEqual(result.reason, "entry_supported_exit_missing")

    def test_replay_accepts_later_fee_paid_exit(self) -> None:
        trades = [
            TradePrint(price=100.0, size=0.25, ts=1.0, side="s", trade_id=1),
            TradePrint(price=100.0, size=0.25, ts=2.0, side="s", trade_id=2),
            TradePrint(price=101.0, size=0.5, ts=3.0, side="b", trade_id=3),
        ]

        result = router.replay_recent_roundtrip(
            trades,
            buy_price=100.0,
            target_price=101.0,
            allocation_usd=50.0,
            participation=1.0,
        )

        self.assertTrue(result.entry_ok)
        self.assertTrue(result.exit_ok)
        self.assertEqual(result.entry_trade_count, 2)
        self.assertEqual(result.exit_trade_count, 1)
        self.assertAlmostEqual(result.entry_notional, 50.0, places=8)
        self.assertAlmostEqual(result.exit_notional, 50.5, places=8)

    def test_replay_respects_participation(self) -> None:
        trades = [
            TradePrint(price=100.0, size=0.5, ts=1.0, side="s", trade_id=1),
            TradePrint(price=101.0, size=0.5, ts=2.0, side="b", trade_id=2),
        ]

        result = router.replay_recent_roundtrip(
            trades,
            buy_price=100.0,
            target_price=101.0,
            allocation_usd=50.0,
            participation=0.5,
        )

        self.assertFalse(result.entry_ok)
        self.assertFalse(result.exit_ok)
        self.assertEqual(result.reason, "partial_entry_only")
        self.assertAlmostEqual(result.entry_qty, 0.25, places=8)

    def test_recent_trades_filters_by_timestamp(self) -> None:
        trades = [
            TradePrint(price=100.0, size=1.0, ts=10.0, side="s"),
            TradePrint(price=100.0, size=1.0, ts=20.0, side="s"),
        ]

        filtered = router.recent_trades(trades, lookback_seconds=5.0, now=20.0)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].ts, 20.0)

    def test_product_row_blocks_stale_roundtrip(self) -> None:
        pair = KrakenPair(
            rest_pair="TESTUSD",
            altname="TESTUSD",
            wsname="TEST/USD",
            base="TEST",
            quote="USD",
            order_min=1.0,
            cost_min=1.0,
            tick_size=0.01,
            lot_decimals=4,
            pair_decimals=2,
            status="online",
        )
        args = type(
            "Args",
            (),
            {
                "initial_capital": 50.0,
                "levels": 5,
                "entry_offset_mult": 0.0,
                "spacing_bps": 100.0,
                "trade_volume_participation": 1.0,
                "maker_fee_bps": 25.0,
                "min_net_edge_bps": 0.0,
                "max_roundtrip_seconds": 60.0,
                "max_signal_age_seconds": 0.0,
            },
        )()
        trades = [
            TradePrint(price=100.0, size=1.0, ts=1.0, side="s", trade_id=1),
            TradePrint(price=101.0, size=1.0, ts=120.0, side="b", trade_id=2),
        ]

        row = router.product_row(
            product_id="TEST-USD",
            pair=pair,
            volume_24h_usd=1000.0,
            bid=100.0,
            ask=100.0,
            trades=trades,
            args=args,
            now=130.0,
        )

        self.assertTrue(row["roundtrip_exit_ok"])
        self.assertIn("stale_roundtrip", row["blockers"])

    def test_product_row_blocks_stale_signal(self) -> None:
        pair = KrakenPair(
            rest_pair="TESTUSD",
            altname="TESTUSD",
            wsname="TEST/USD",
            base="TEST",
            quote="USD",
            order_min=1.0,
            cost_min=1.0,
            tick_size=0.01,
            lot_decimals=4,
            pair_decimals=2,
            status="online",
        )
        args = type(
            "Args",
            (),
            {
                "initial_capital": 50.0,
                "levels": 5,
                "entry_offset_mult": 0.0,
                "spacing_bps": 100.0,
                "trade_volume_participation": 1.0,
                "maker_fee_bps": 25.0,
                "min_net_edge_bps": 0.0,
                "max_roundtrip_seconds": 300.0,
                "max_signal_age_seconds": 30.0,
            },
        )()
        trades = [
            TradePrint(price=100.0, size=1.0, ts=1.0, side="s", trade_id=1),
            TradePrint(price=101.0, size=1.0, ts=20.0, side="b", trade_id=2),
        ]

        row = router.product_row(
            product_id="TEST-USD",
            pair=pair,
            volume_24h_usd=1000.0,
            bid=100.0,
            ask=100.0,
            trades=trades,
            args=args,
            now=100.0,
        )

        self.assertTrue(row["roundtrip_exit_ok"])
        self.assertIn("stale_signal", row["blockers"])


if __name__ == "__main__":
    unittest.main()
