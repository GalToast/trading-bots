#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_grid_adaptive_router_shadow as adaptive
from run_kraken_vulture_trigger_tape import Book, Level


class KrakenGridAdaptiveRouterShadowTests(unittest.TestCase):
    def test_best_fire_candidate_skips_blocked_and_non_exit_rows(self) -> None:
        payload = {
            "rows": [
                {"product_id": "AAA-USD", "roundtrip_exit_ok": True, "blockers": ["stale_roundtrip"]},
                {"product_id": "BBB-USD", "roundtrip_exit_ok": False, "blockers": []},
                {"product_id": "CCC-USD", "roundtrip_exit_ok": True, "blockers": []},
            ]
        }

        row = adaptive.best_fire_candidate(payload)

        self.assertIsNotNone(row)
        self.assertEqual(row["product_id"], "CCC-USD")

    def test_best_fire_candidate_honors_cooldown(self) -> None:
        payload = {
            "rows": [
                {"product_id": "AAA-USD", "roundtrip_exit_ok": True, "blockers": []},
                {"product_id": "BBB-USD", "roundtrip_exit_ok": True, "blockers": []},
            ]
        }

        row = adaptive.best_fire_candidate(payload, cooldowns={"AAA-USD": 200.0}, now=100.0)

        self.assertIsNotNone(row)
        self.assertEqual(row["product_id"], "BBB-USD")

    def test_standing_bid_from_row_preserves_router_prices(self) -> None:
        row = {
            "product_id": "CC-USD",
            "rest_pair": "CCUSD",
            "buy_price": 0.14851,
            "target_price": 0.1494,
            "allocation_usd": 10.0,
        }

        bid = adaptive.standing_bid_from_row(row, standing_seconds=60.0)

        self.assertEqual(bid.product_id, "CC-USD")
        self.assertEqual(bid.rest_pair, "CCUSD")
        self.assertAlmostEqual(bid.qty, 10.0 / 0.14851, places=8)
        self.assertGreater(bid.expires_at, 0.0)

    def test_position_from_bid_charges_maker_fee(self) -> None:
        row = {
            "product_id": "CC-USD",
            "rest_pair": "CCUSD",
            "buy_price": 0.14851,
            "target_price": 0.1494,
            "allocation_usd": 10.0,
        }
        bid = adaptive.standing_bid_from_row(row, standing_seconds=60.0)

        position = adaptive.position_from_bid(bid, maker_fee_bps=25.0)

        self.assertAlmostEqual(position.cost_usd, 10.0, places=8)
        self.assertAlmostEqual(position.buy_fee_usd, 0.025, places=8)
        self.assertAlmostEqual(position.target_price, 0.1494, places=8)

    def test_summary_marks_inventory_conservatively(self) -> None:
        row = {
            "product_id": "CC-USD",
            "rest_pair": "CCUSD",
            "buy_price": 100.0,
            "target_price": 101.0,
            "allocation_usd": 10.0,
        }
        bid = adaptive.standing_bid_from_row(row, standing_seconds=60.0)
        position = adaptive.position_from_bid(bid, maker_fee_bps=25.0)
        book = Book(bid=100.0, ask=100.1, bids=[Level(price=100.0, size=1.0)], asks=[Level(price=100.1, size=1.0)])

        payload = adaptive.summary_payload(
            started_at="2026-04-27T00:00:00+00:00",
            cash=39.975,
            initial_capital=50.0,
            active_bid=None,
            active_product="CC-USD",
            position=position,
            book=book,
            realized_net=0.0,
            fees=0.025,
            router_scans=1,
            fire_candidates=1,
            standing_bids=1,
            bid_expirations=0,
            buys=1,
            target_closes=0,
            sweep_closes=0,
            blocked_entries=0,
            blocked_exits=0,
            no_fire_scans=0,
            taker_fee_bps=40.0,
            liquidation_haircut_bps=10.0,
        )

        self.assertEqual(payload["open_positions"], 1)
        self.assertLess(payload["open_inventory_pnl"], 0.0)
        self.assertLess(payload["return_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
