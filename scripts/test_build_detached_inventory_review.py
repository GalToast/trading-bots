#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_detached_inventory_review as review


class BuildDetachedInventoryReviewTests(unittest.TestCase):
    def test_build_payload_combines_active_and_historical_detached_inventory(self) -> None:
        live_magic_scope_payload = {
            "account_snapshot": {
                "equity_usd": 68532.52,
                "balance_usd": 68116.85,
                "profit_usd": 415.67,
                "position_count": 51,
            },
            "rows": [
                {
                    "lane": "live_rearm_941777",
                    "outside_scope_open_count": 39,
                    "outside_scope_profit_usd": 172.29,
                    "outside_scope_symbols": {"USDJPY": 39},
                    "oldest_outside_scope_opened_at": "2026-04-13T03:05:36+00:00",
                    "recommended_action": "manual_review_do_not_autoclose",
                },
                {
                    "lane": "live_momentum_alpha50_941778",
                    "outside_scope_open_count": 0,
                    "outside_scope_profit_usd": 0.0,
                    "outside_scope_symbols": {},
                    "oldest_outside_scope_opened_at": "",
                    "recommended_action": "none",
                },
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
                    "opened_at": "2026-04-12T04:51:38+00:00",
                }
            ],
        }
        mt5_visibility_payload = {
            "summary": {
                "live_lanes_visible_now": 2,
                "enabled_live_lane_count": 5,
                "disabled_live_lane_count": 3,
            }
        }
        live_m5_portfolio_payload = {
            "ghost_rows": [
                {
                    "lane": "live_btcusd_m5_warp_probation_941780",
                    "symbol": "BTCUSD",
                    "live_magic": 941780,
                    "audit_state": "stale_or_cleared",
                    "position_count": 6,
                    "floating_usd": -139.8,
                },
                {
                    "lane": "live_solusd_m5_warp_941783",
                    "symbol": "SOLUSD",
                    "live_magic": 941783,
                    "audit_state": "active",
                    "position_count": 3,
                    "floating_usd": -12.6,
                },
            ]
        }

        payload = review.build_payload(
            live_magic_scope_payload,
            mt5_visibility_payload,
            live_m5_portfolio_payload,
        )

        summary = payload["summary"]
        self.assertEqual(summary["active_detached_positions"], 40)
        self.assertEqual(summary["active_legacy_positions"], 39)
        self.assertEqual(summary["unassigned_live_symbol_positions"], 1)
        self.assertEqual(summary["active_detached_profit_usd"], 471.01)
        self.assertEqual(summary["active_detached_live_pnl_share_pct"], 113.3)
        self.assertEqual(summary["historical_ghost_positions"], 9)
        self.assertEqual(summary["active_ghost_positions"], 3)
        self.assertEqual(summary["stale_ghost_positions"], 6)
        self.assertEqual(summary["needs_human_decision_count"], 2)

        decision_items = {row["item"]: row for row in payload["decision_queue"]}
        self.assertEqual(decision_items["live_rearm_941777_legacy_outside_scope"]["decision"], "carry_vs_close")
        self.assertEqual(decision_items["unassigned_live_symbol_inventory"]["decision"], "attribute_vs_close")
        self.assertEqual(decision_items["historical_ghost_carry_audit"]["status"], "non_blocking")

    def test_render_markdown_mentions_active_and_historical_sections(self) -> None:
        payload = {
            "generated_at": "2026-04-15T21:31:16+00:00",
            "account_snapshot": {
                "equity_usd": 68532.52,
                "balance_usd": 68116.85,
                "profit_usd": 415.67,
                "position_count": 51,
            },
            "summary": {
                "active_detached_positions": 40,
                "active_detached_profit_usd": 471.01,
                "active_legacy_positions": 39,
                "unassigned_live_symbol_positions": 1,
                "historical_ghost_positions": 9,
                "needs_human_decision_count": 2,
                "active_detached_live_pnl_share_pct": 113.3,
                "historical_ghost_profit_usd": -152.4,
                "active_ghost_positions": 0,
                "stale_ghost_positions": 9,
            },
            "active_legacy_rows": [
                {
                    "lane": "live_rearm_941777",
                    "outside_scope_symbols": {"USDJPY": 39},
                    "outside_scope_open_count": 39,
                    "outside_scope_profit_usd": 172.29,
                    "oldest_outside_scope_opened_at": "2026-04-13T03:05:36+00:00",
                    "recommended_action": "manual_review_do_not_autoclose",
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
                    "opened_at": "2026-04-12T04:51:38+00:00",
                }
            ],
            "ghost_rows": [
                {
                    "lane": "live_btcusd_m5_warp_probation_941780",
                    "symbol": "BTCUSD",
                    "live_magic": 941780,
                    "audit_state": "stale_or_cleared",
                    "position_count": 6,
                    "floating_usd": -139.8,
                }
            ],
            "decision_queue": [
                {
                    "item": "live_rearm_941777_legacy_outside_scope",
                    "bucket": "active_detached_inventory",
                    "decision": "carry_vs_close",
                    "status": "needs_human_review",
                    "positions": 39,
                    "floating_pnl_usd": 172.29,
                    "read": "example",
                }
            ],
        }

        markdown = review.render_markdown(payload)

        self.assertIn("Detached Inventory Review", markdown)
        self.assertIn("Active Detached Inventory Affecting MT5 Now", markdown)
        self.assertIn("Historical Ghost Carry Reference", markdown)
        self.assertIn("Decision Queue", markdown)
        self.assertIn("live_rearm_941777", markdown)


if __name__ == "__main__":
    unittest.main()
