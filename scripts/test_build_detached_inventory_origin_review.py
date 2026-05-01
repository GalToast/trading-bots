#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_detached_inventory_origin_review as review


class BuildDetachedInventoryOriginReviewTests(unittest.TestCase):
    def test_classify_origin_distinguishes_live_lane_inventory_and_magic_zero(self) -> None:
        origin_class, origin_read = review.classify_origin(
            {
                "bucket": "active_legacy_outside_scope",
                "magic": 941777,
                "comment": "PLIVE-LATTICE-S",
            },
            {
                "magic": 941777,
                "comment": "PLIVE-LATTICE-S",
                "reason_name": "DEAL_REASON_EXPERT",
            },
            {},
        )
        self.assertEqual(origin_class, "prior_live_lane_inventory")
        self.assertIn("prior lattice live inventory", origin_read)

        origin_class, origin_read = review.classify_origin(
            {
                "bucket": "unassigned_live_symbol",
                "magic": 0,
                "comment": "",
            },
            {
                "magic": 0,
                "comment": "",
                "reason_name": "DEAL_REASON_CLIENT",
            },
            {},
        )
        self.assertEqual(origin_class, "manual_or_terminal_open_magic_zero")
        self.assertIn("client-side", origin_read)

    def test_build_payload_with_fake_history_adds_origin_rows(self) -> None:
        class FakeMt5:
            DEAL_REASON_CLIENT = 3
            DEAL_REASON_EXPERT = 4
            DEAL_ENTRY_IN = 0
            ORDER_REASON_CLIENT = 3
            ORDER_TYPE_BUY = 0

            def history_deals_get(self, start, end):
                return [
                    type(
                        "Deal",
                        (),
                        {
                            "ticket": 7001,
                            "order": 8001,
                            "position_id": 45913027,
                            "symbol": "USDJPY",
                            "magic": 941777,
                            "comment": "PLIVE-LATTICE-S",
                            "reason": self.DEAL_REASON_EXPERT,
                            "entry": self.DEAL_ENTRY_IN,
                            "volume": 0.01,
                            "price": 159.733,
                            "profit": 0.0,
                            "time": 1776049536,
                        },
                    )(),
                    type(
                        "Deal",
                        (),
                        {
                            "ticket": 7002,
                            "order": 8002,
                            "position_id": 45912807,
                            "symbol": "BTCUSD",
                            "magic": 0,
                            "comment": "",
                            "reason": self.DEAL_REASON_CLIENT,
                            "entry": self.DEAL_ENTRY_IN,
                            "volume": 0.01,
                            "price": 71585.66,
                            "profit": 0.0,
                            "time": 1775950298,
                        },
                    )(),
                ]

            def history_orders_get(self, start, end):
                return [
                    type(
                        "Order",
                        (),
                        {
                            "ticket": 8001,
                            "position_id": 45913027,
                            "symbol": "USDJPY",
                            "magic": 941777,
                            "comment": "PLIVE-LATTICE-S",
                            "reason": self.ORDER_REASON_CLIENT,
                            "type": self.ORDER_TYPE_BUY,
                            "volume_initial": 0.01,
                            "price_open": 159.733,
                            "time_setup": 1776049536,
                            "time_done": 1776049536,
                        },
                    )(),
                    type(
                        "Order",
                        (),
                        {
                            "ticket": 8002,
                            "position_id": 45912807,
                            "symbol": "BTCUSD",
                            "magic": 0,
                            "comment": "",
                            "reason": self.ORDER_REASON_CLIENT,
                            "type": self.ORDER_TYPE_BUY,
                            "volume_initial": 0.01,
                            "price_open": 71585.66,
                            "time_setup": 1775950298,
                            "time_done": 1775950298,
                        },
                    )(),
                ]

            def shutdown(self):
                return None

        live_magic_scope_payload = {
            "account_snapshot": {
                "equity_usd": 68532.52,
                "balance_usd": 68116.85,
                "profit_usd": 415.67,
            },
            "rows": [
                {
                    "lane": "live_rearm_941777",
                    "live_magic": 941777,
                    "outside_scope_positions": [
                        {
                            "ticket": 45913027,
                            "symbol": "USDJPY",
                            "side": "SELL",
                            "volume": 0.01,
                            "price_open": 159.733,
                            "profit_usd": 4.42,
                            "comment": "PLIVE-LATTICE-S",
                            "opened_at": "2026-04-13T03:05:36+00:00",
                        }
                    ],
                }
            ],
            "unassigned_live_symbol_positions": [
                {
                    "ticket": 45912807,
                    "symbol": "BTCUSD",
                    "magic": 0,
                    "side": "BUY",
                    "volume": 0.01,
                    "price_open": 71585.66,
                    "profit_usd": 298.72,
                    "comment": "",
                    "opened_at": "2026-04-12T04:51:38+00:00",
                }
            ],
        }
        detached_review_payload = {
            "summary": {
                "active_detached_positions": 40,
                "active_detached_profit_usd": 471.01,
                "active_detached_live_pnl_share_pct": 113.3,
            }
        }

        original_init = review.mt5_terminal_guard.initialize_mt5
        review.mt5_terminal_guard.initialize_mt5 = lambda mt5_module: (True, {"identity_ok": True, "reason": "ok"})
        try:
            payload = review.build_payload(
                live_magic_scope_payload,
                detached_review_payload,
                mt5_module=FakeMt5(),
            )
        finally:
            review.mt5_terminal_guard.initialize_mt5 = original_init

        self.assertEqual(payload["summary"]["detached_position_count"], 2)
        self.assertEqual(payload["summary"]["origin_counts"]["prior_live_lane_inventory"], 1)
        self.assertEqual(payload["summary"]["origin_counts"]["manual_or_terminal_open_magic_zero"], 1)

        rows_by_ticket = {row["ticket"]: row for row in payload["rows"]}
        self.assertEqual(rows_by_ticket[45913027]["origin_class"], "prior_live_lane_inventory")
        self.assertEqual(rows_by_ticket[45912807]["origin_class"], "manual_or_terminal_open_magic_zero")


if __name__ == "__main__":
    unittest.main()
