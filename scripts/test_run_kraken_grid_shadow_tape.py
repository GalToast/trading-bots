#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_grid_shadow_tape as tape
from run_kraken_vulture_trigger_tape import Book, Level


def book(*, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> Book:
    bid_levels = [Level(price=price, size=size) for price, size in bids]
    ask_levels = [Level(price=price, size=size) for price, size in asks]
    return Book(bid=bid_levels[0].price, ask=ask_levels[0].price, bids=bid_levels, asks=ask_levels)


def position(*, qty: float = 1.0, cost_usd: float = 100.0, buy_fee_usd: float = 0.25) -> tape.GridPosition:
    return tape.GridPosition(
        level=1,
        buy_price=cost_usd / qty,
        target_price=(cost_usd / qty) * 1.02,
        qty=qty,
        cost_usd=cost_usd,
        buy_fee_usd=buy_fee_usd,
        opened_at="2026-04-27T00:00:00+00:00",
        opened_ts=0.0,
    )


class KrakenGridShadowTapeTests(unittest.TestCase):
    def test_buy_fill_requires_limit_cross_and_sufficient_depth(self) -> None:
        asks = [Level(price=99.0, size=0.5), Level(price=100.0, size=1.0), Level(price=101.0, size=10.0)]

        not_crossed = tape.eligible_buy_fill(asks, limit_price=98.0, quote_usd=100.0)
        self.assertFalse(not_crossed.ok)
        self.assertEqual(not_crossed.reason, "limit_not_crossed")

        shallow = tape.eligible_buy_fill(asks, limit_price=99.0, quote_usd=100.0)
        self.assertFalse(shallow.ok)
        self.assertEqual(shallow.reason, "insufficient_crossed_ask_depth")

        filled = tape.eligible_buy_fill(asks, limit_price=100.0, quote_usd=100.0)
        self.assertTrue(filled.ok)
        self.assertAlmostEqual(filled.gross_quote, 100.0, places=8)
        self.assertLess(filled.avg_price, 100.0)

    def test_sell_fill_requires_limit_cross_and_sufficient_depth(self) -> None:
        bids = [Level(price=102.0, size=0.25), Level(price=101.0, size=1.0), Level(price=100.0, size=10.0)]

        not_crossed = tape.eligible_sell_fill(bids, limit_price=103.0, qty=1.0)
        self.assertFalse(not_crossed.ok)
        self.assertEqual(not_crossed.reason, "limit_not_crossed")

        shallow = tape.eligible_sell_fill(bids, limit_price=102.0, qty=1.0)
        self.assertFalse(shallow.ok)
        self.assertEqual(shallow.reason, "insufficient_bid_depth")

        filled = tape.eligible_sell_fill(bids, limit_price=101.0, qty=1.0)
        self.assertTrue(filled.ok)
        self.assertAlmostEqual(filled.qty, 1.0, places=8)
        self.assertGreater(filled.avg_price, 101.0)

    def test_liquidate_inventory_reports_gross_costs_net_and_pnl(self) -> None:
        px_book = book(bids=[(105.0, 2.0)], asks=[(106.0, 2.0)])
        ok, gross, costs, net, pnl, net_bps, reason = tape.liquidate_inventory(
            [position(qty=1.0, cost_usd=100.0, buy_fee_usd=0.25)],
            px_book,
            taker_fee_bps=40.0,
            haircut_bps=10.0,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "liquidated")
        self.assertAlmostEqual(gross, 105.0, places=8)
        self.assertAlmostEqual(costs, 0.525, places=8)
        self.assertAlmostEqual(net, 104.475, places=8)
        self.assertAlmostEqual(pnl, 4.225, places=8)
        self.assertGreater(net_bps, 400.0)

    def test_trade_tape_buy_and_sell_fills_consume_print_volume(self) -> None:
        trades = [
            tape.TradePrint(price=99.0, size=0.75, ts=1.0, side="s", trade_id=1),
            tape.TradePrint(price=101.0, size=1.0, ts=2.0, side="b", trade_id=2),
        ]

        buy_fill = tape.eligible_buy_trade_fill(trades, limit_price=100.0, quote_usd=50.0, participation=1.0)
        self.assertTrue(buy_fill.ok)
        self.assertAlmostEqual(buy_fill.qty, 0.5, places=8)
        self.assertAlmostEqual(buy_fill.gross_quote, 50.0, places=8)
        self.assertAlmostEqual(trades[0].size, 0.25, places=8)

        buy_shallow = tape.eligible_buy_trade_fill(trades, limit_price=100.0, quote_usd=50.0, participation=1.0)
        self.assertFalse(buy_shallow.ok)
        self.assertEqual(buy_shallow.reason, "insufficient_trade_tape_qty")

        sell_fill = tape.eligible_sell_trade_fill(trades, limit_price=100.0, qty=0.5, participation=1.0)
        self.assertTrue(sell_fill.ok)
        self.assertAlmostEqual(sell_fill.gross_quote, 50.0, places=8)
        self.assertAlmostEqual(trades[1].size, 0.5, places=8)

    def test_parse_trades_reads_kraken_public_shape(self) -> None:
        payload = {
            "BLENDUSD": [
                ["0.084420000", "85.79355", 1777311601.2999766, "s", "l", "", 26717],
            ],
            "last": "1777311601299976704",
        }

        trades = tape.parse_trades(payload, rest_pair="BLENDUSD")

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].side, "s")
        self.assertEqual(trades[0].trade_id, 26717)
        self.assertAlmostEqual(trades[0].price, 0.08442, places=8)

    def test_state_payload_marks_open_inventory_after_liquidation_costs(self) -> None:
        px_book = book(bids=[(100.0, 2.0)], asks=[(101.0, 2.0)])
        payload = tape.state_payload(
            product_id="TEST-USD",
            cash=0.0,
            positions=[position(qty=1.0, cost_usd=100.0, buy_fee_usd=0.25)],
            book=px_book,
            initial_capital=100.25,
            realized_net=0.0,
            fees=0.25,
            buys=1,
            target_closes=0,
            sweep_closes=0,
            sweep_count=0,
            blocked_entries=0,
            blocked_exits=0,
            blocked_entry_reasons={},
            started_at="2026-04-27T00:00:00+00:00",
            taker_fee_bps=40.0,
            liquidation_haircut_bps=10.0,
            anchor=100.0,
            spacing_bps=80.0,
            levels=2,
            entry_offset_mult=0.2,
        )

        self.assertAlmostEqual(payload["open_inventory_value"], 99.5, places=8)
        self.assertAlmostEqual(payload["open_inventory_mark_costs"], 0.5, places=8)
        self.assertAlmostEqual(payload["open_inventory_pnl"], -0.75, places=8)
        self.assertLess(payload["return_pct"], 0.0)
        self.assertEqual(payload["next_buy_prices"], [99.84, 99.04])


if __name__ == "__main__":
    unittest.main()
