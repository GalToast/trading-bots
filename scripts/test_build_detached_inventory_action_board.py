#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_detached_inventory_action_board as board


class BuildDetachedInventoryActionBoardTests(unittest.TestCase):
    def test_build_payload_generates_action_commands_and_scenarios(self) -> None:
        detached_review_payload = {
            "account_snapshot": {
                "equity_usd": 68532.52,
                "balance_usd": 68116.85,
                "profit_usd": 415.67,
                "position_count": 51,
            },
            "summary": {
                "active_detached_positions": 40,
                "active_detached_profit_usd": 471.01,
                "active_detached_live_pnl_share_pct": 113.3,
            },
            "decision_queue": [
                {
                    "item": "live_rearm_941777_legacy_outside_scope",
                    "bucket": "active_detached_inventory",
                    "decision": "carry_vs_close",
                    "status": "needs_human_review",
                    "positions": 39,
                    "floating_pnl_usd": 172.29,
                    "read": "legacy USDJPY carry",
                    "recommended_action": "manual_review_do_not_autoclose",
                    "oldest_opened_at": "2026-04-13T03:05:36+00:00",
                },
                {
                    "item": "unassigned_live_symbol_inventory",
                    "bucket": "active_detached_inventory",
                    "decision": "attribute_vs_close",
                    "status": "needs_human_review",
                    "positions": 1,
                    "floating_pnl_usd": 298.72,
                    "read": "manual BTC magic-zero",
                    "recommended_action": "review_magic_origin_before_close",
                    "oldest_opened_at": "2026-04-12T04:51:38+00:00",
                },
            ],
        }
        detached_origin_payload = {
            "mt5_connection": {"identity_ok": True, "reason": "ok"},
            "summary": {
                "origin_counts": {
                    "manual_or_terminal_open_magic_zero": 1,
                    "prior_live_lane_inventory": 39,
                }
            },
            "rows": [
                {
                    "bucket": "active_legacy_outside_scope",
                    "owner_lane": "live_rearm_941777",
                    "ticket": 45913027,
                    "symbol": "USDJPY",
                    "magic": 941777,
                    "profit_usd": 172.29,
                    "comment": "PLIVE-LATTICE-S",
                    "opened_at": "2026-04-13T03:05:36+00:00",
                    "origin_class": "prior_live_lane_inventory",
                    "origin_read": "prior live inventory",
                },
                {
                    "bucket": "unassigned_live_symbol",
                    "owner_lane": "",
                    "ticket": 45912807,
                    "symbol": "BTCUSD",
                    "magic": 0,
                    "profit_usd": 298.72,
                    "comment": "",
                    "opened_at": "2026-04-12T04:51:38+00:00",
                    "origin_class": "manual_or_terminal_open_magic_zero",
                    "origin_read": "client-side magic zero",
                },
            ],
        }

        payload = board.build_payload(detached_review_payload, detached_origin_payload)

        self.assertEqual(payload["summary"]["action_item_count"], 2)
        items = {row["item"]: row for row in payload["action_items"]}
        self.assertIn("--magic 941777 --symbol USDJPY --comment-contains PLIVE-LATTICE-S --expect-count 1", items["live_rearm_941777_legacy_outside_scope"]["dry_run_command"])
        self.assertIn("--ticket 45912807 --expect-count 1", items["unassigned_live_symbol_inventory"]["dry_run_command"])
        self.assertIn("--apply", items["unassigned_live_symbol_inventory"]["apply_command"])
        self.assertEqual(
            items["unassigned_live_symbol_inventory"]["dry_run_argv"],
            ["python", "scripts/operators/mt5_close_filtered.py", "--ticket", "45912807", "--expect-count", "1"],
        )

        scenarios = {row["scenario_id"]: row for row in payload["scenarios"]}
        self.assertEqual(scenarios["close_manual_btc_only"]["remaining_detached_positions"], 39)
        self.assertEqual(scenarios["close_manual_btc_only"]["remaining_detached_pnl_usd"], 172.29)
        self.assertEqual(scenarios["close_legacy_usdjpy_only"]["remaining_detached_positions"], 1)
        self.assertEqual(scenarios["close_legacy_usdjpy_only"]["remaining_detached_pnl_usd"], 298.72)
        self.assertEqual(scenarios["close_all_active_detached"]["remaining_detached_positions"], 0)
        self.assertEqual(scenarios["close_all_active_detached"]["remaining_detached_pnl_usd"], 0.0)

    def test_render_markdown_mentions_commands_and_scenarios(self) -> None:
        payload = {
            "generated_at": "2026-04-15T21:50:00+00:00",
            "mt5_connection": {"identity_ok": True, "reason": "ok"},
            "account_snapshot": {
                "equity_usd": 68532.52,
                "balance_usd": 68116.85,
                "profit_usd": 415.67,
                "position_count": 51,
            },
            "summary": {
                "active_detached_positions": 40,
                "active_detached_profit_usd": 471.01,
                "active_detached_live_pnl_share_pct": 113.3,
                "action_item_count": 2,
            },
            "action_items": [
                {
                    "item": "unassigned_live_symbol_inventory",
                    "status": "needs_human_review",
                    "owner_lane": "",
                    "symbols": {"BTCUSD": 1},
                    "magic": 0,
                    "expected_match_count": 1,
                    "floating_pnl_usd": 298.72,
                    "operator_read": "manual BTC magic-zero",
                    "origin_reads": ["client-side magic zero"],
                    "origin_classes": {"manual_or_terminal_open_magic_zero": 1},
                    "dry_run_command": "python scripts/operators/mt5_close_filtered.py --ticket 45912807 --expect-count 1",
                    "apply_command": "python scripts/operators/mt5_close_filtered.py --ticket 45912807 --expect-count 1 --apply",
                }
            ],
            "scenarios": [
                {
                    "label": "Close manual/client BTC magic-zero inventory only",
                    "removed_positions": 1,
                    "removed_pnl_usd": 298.72,
                    "remaining_detached_positions": 39,
                    "remaining_detached_pnl_usd": 172.29,
                    "remaining_detached_live_pnl_share_pct": 41.4,
                }
            ],
            "refresh_commands": ["python scripts/build_detached_inventory_action_board.py"],
            "historical_reference_note": "history note",
        }

        markdown = board.render_markdown(payload)

        self.assertIn("Detached Inventory Action Board", markdown)
        self.assertIn("Action Queue", markdown)
        self.assertIn("Scenario Impact", markdown)
        self.assertIn("Post-Action Refresh", markdown)
        self.assertIn("python scripts/operators/mt5_close_filtered.py --ticket 45912807 --expect-count 1", markdown)
        self.assertIn("Close manual/client BTC magic-zero inventory only", markdown)


if __name__ == "__main__":
    unittest.main()
