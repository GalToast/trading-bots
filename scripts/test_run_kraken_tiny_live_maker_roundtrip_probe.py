#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_tiny_live_maker_roundtrip_probe as probe


class KrakenTinyLiveMakerRoundtripProbeTests(unittest.TestCase):
    def test_book_snapshot_computes_depth_and_vwap(self) -> None:
        class FakeClient:
            def depth(self, rest_pair: str, count: int = 20) -> dict:
                self.last_count = count
                return {
                    rest_pair: {
                        "bids": [["9.90", "1"], ["9.80", "2"]],
                        "asks": [["10.00", "1"], ["10.20", "2"]],
                    }
                }

        class FakePair:
            rest_pair = "TESTUSD"
            wsname = "TEST/USD"
            quote = "USD"

        row = probe.book_snapshot(
            FakeClient(),
            FakePair(),  # type: ignore[arg-type]
            quote_amount=15.0,
            base_volume=1.5,
            depth_count=10,
        )

        self.assertTrue(row["book_buy_depth_ok"])
        self.assertTrue(row["book_sell_depth_ok"])
        self.assertAlmostEqual(row["book_l10_imbalance_ratio"], 29.5 / 30.4, places=6)
        self.assertGreater(row["book_buy_vwap"], row["book_ask"])
        self.assertLess(row["book_sell_vwap"], row["book_bid"])

    def test_inside_spread_buy_never_crosses_ask(self) -> None:
        price = probe.legal_maker_buy_price(0.01795, 0.01815, 0.00001, improve_ticks=50)

        self.assertEqual(price, 0.01814)
        self.assertLess(price, 0.01815)

    def test_zero_improve_buy_joins_bid(self) -> None:
        price = probe.legal_maker_buy_price(0.01795, 0.01815, 0.00001, improve_ticks=0)

        self.assertEqual(price, 0.01795)

    def test_inside_spread_sell_respects_profit_floor_and_does_not_cross_bid(self) -> None:
        price = probe.legal_maker_sell_price(
            0.3256,
            0.3264,
            0.0001,
            minimum_price=0.32565,
            inside_spread=True,
        )

        self.assertEqual(price, 0.3257)
        self.assertGreater(price, 0.3256)

    def test_inside_spread_sell_uses_required_price_when_floor_above_ask(self) -> None:
        price = probe.legal_maker_sell_price(
            0.3256,
            0.3264,
            0.0001,
            minimum_price=0.32701,
            inside_spread=True,
        )

        self.assertEqual(price, 0.3271)

    def test_default_sell_joins_ask_or_better(self) -> None:
        price = probe.legal_maker_sell_price(
            0.3256,
            0.3264,
            0.0001,
            minimum_price=0.32565,
            inside_spread=False,
        )

        self.assertEqual(price, 0.3264)

    def test_exit_floor_price_includes_both_fees_and_target_net(self) -> None:
        price, raw_price = probe.maker_exit_floor_price(
            entry_cost=9.0,
            entry_fee=9.0 * 0.0025,
            volume=504.48430493,
            maker_fee_bps=25.0,
            target_net_pct=0.10,
            tick_size=0.00001,
        )

        self.assertAlmostEqual(raw_price, 0.017947353, places=9)
        self.assertEqual(price, 0.01795)

    def test_exit_floor_above_ask_bps_reports_only_unreachable_distance(self) -> None:
        self.assertAlmostEqual(probe.exit_floor_above_ask_bps(0.01795, 0.01785), 56.022409, places=6)
        self.assertEqual(probe.exit_floor_above_ask_bps(0.01780, 0.01785), 0.0)

    def test_normalized_balance_amounts_merges_kraken_aliases(self) -> None:
        balances = probe.normalized_balance_amounts({"XXBT": "0.10", "XBT": "0.02", "ZUSD": "3.50"})

        self.assertAlmostEqual(balances["BTC"], 0.12)
        self.assertAlmostEqual(balances["USD"], 3.5)

    def test_pressure_gate_requires_floor_enforced_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            path.write_text(
                '{"parameters":{"enforce_sell_floor_from_queue":false},'
                '"leaders":[{"key":"HONEY-USD|0.0000","cycles":8,"two_sided_fill_rate":0.5}]}',
                encoding="utf-8",
            )

            status = probe.pressure_gate_status(
                path,
                product_id="HONEY-USD",
                min_cycles=5,
                min_two_sided_rate=0.25,
                require_sell_floor=True,
            )

        self.assertFalse(status["ok"])
        self.assertEqual(status["reason"], "pressure_summary_not_sell_floor_enforced")

    def test_pressure_gate_passes_floor_enforced_repeated_two_sided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            path.write_text(
                '{"parameters":{"enforce_sell_floor_from_queue":true},'
                '"leaders":[{"key":"HONEY-USD|0.0000","cycles":8,"two_sided_fill_rate":0.375,"two_sided_depth_ok_rate":1.0}]}',
                encoding="utf-8",
            )

            status = probe.pressure_gate_status(
                path,
                product_id="HONEY-USD",
                min_cycles=5,
                min_two_sided_rate=0.25,
                require_sell_floor=True,
            )

        self.assertTrue(status["ok"])
        self.assertEqual(status["reason"], "pressure_gate_passed")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
