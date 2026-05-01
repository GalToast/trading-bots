#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_mt5_user_visibility_board as board


class BuildMt5UserVisibilityBoardTests(unittest.TestCase):
    def test_sanitize_notes_drops_stale_outside_scope_hint_when_broker_is_clean(self) -> None:
        self.assertEqual(
            board.sanitize_notes(
                "broker_sync_inherited_closes=367/+652.83, broker_scope_outside_lane=USDJPY:39, fx_grad=live progress=graduated(100.0%)",
                outside_scope_open_count=0,
            ),
            "broker_sync_inherited_closes=367/+652.83, fx_grad=live progress=graduated(100.0%)",
        )
        self.assertIn(
            "broker_scope_outside_lane=USDJPY:39",
            board.sanitize_notes(
                "broker_scope_outside_lane=USDJPY:39, fx_grad=live",
                outside_scope_open_count=39,
            ),
        )

    def test_build_payload_classifies_live_and_shadow_visibility(self) -> None:
        live_scope_payload = {
            "rows": [
                {
                    "lane": "live_alpha",
                    "kind": "live_fx",
                    "live_magic": 1001,
                    "scoped_symbols": ["EURUSD"],
                    "managed_open_count": 4,
                    "broker_scoped_open_count": 4,
                    "broker_total_open_count": 4,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "aligned",
                    "recommended_action": "none",
                    "notes": "healthy",
                },
                {
                    "lane": "live_beta",
                    "kind": "live_fx",
                    "live_magic": 1002,
                    "scoped_symbols": ["BTCUSD"],
                    "managed_open_count": 6,
                    "broker_scoped_open_count": 0,
                    "broker_total_open_count": 0,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "scoped_mismatch",
                    "recommended_action": "inspect",
                    "notes": "managed carry only",
                },
                {
                    "lane": "live_gamma",
                    "kind": "live_fx",
                    "live_magic": 1003,
                    "scoped_symbols": ["GBPUSD"],
                    "managed_open_count": 0,
                    "broker_scoped_open_count": 0,
                    "broker_total_open_count": 0,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "aligned",
                    "recommended_action": "none",
                    "notes": "flat",
                },
                {
                    "lane": "live_delta",
                    "kind": "live_fx",
                    "live_magic": 1004,
                    "scoped_symbols": ["EURUSD", "GBPUSD"],
                    "managed_open_count": 2,
                    "broker_scoped_open_count": 2,
                    "broker_total_open_count": 5,
                    "outside_scope_open_count": 3,
                    "outside_scope_symbols": {"USDJPY": 3},
                    "scope_status": "outside_scope_legacy_inventory",
                    "recommended_action": "cleanup",
                    "notes": "legacy inventory",
                    "outside_scope_profit_usd": 12.34,
                },
                {
                    "lane": "live_epsilon",
                    "kind": "live_fx",
                    "live_magic": 1005,
                    "scoped_symbols": ["XAUUSD"],
                    "managed_open_count": 1,
                    "broker_scoped_open_count": 1,
                    "broker_total_open_count": 1,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "aligned",
                    "recommended_action": "none",
                    "notes": "broker visible but runtime stale",
                },
                {
                    "lane": "live_zeta",
                    "kind": "live_fx",
                    "enabled": False,
                    "pause_note": "decommissioned_for_review",
                    "live_magic": 1006,
                    "scoped_symbols": ["ETHUSD"],
                    "managed_open_count": 0,
                    "broker_scoped_open_count": 0,
                    "broker_total_open_count": 0,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "aligned",
                    "recommended_action": "none",
                    "notes": "disabled lane",
                },
            ]
        }
        execution_monitor_payload = {
            "rows": [
                {"lane": "live_alpha", "watchdog_status": "ok", "state_last_write_at": "2026-04-15T00:00:00+00:00", "event_last_write_at": "x", "heartbeat_at": "y"},
                {"lane": "live_beta", "watchdog_status": None, "state_last_write_at": None, "event_last_write_at": None, "heartbeat_at": None},
                {"lane": "live_delta", "watchdog_status": "ok", "state_last_write_at": "2026-04-15T00:00:00+00:00", "event_last_write_at": "x", "heartbeat_at": "y"},
                {"lane": "live_epsilon", "watchdog_status": None, "state_last_write_at": None, "event_last_write_at": None, "heartbeat_at": None},
            ]
        }
        shadow_deploy_payload = {
            "name": "shadow_gbpusd_m15_hungry_hippo_v1",
            "kind": "shadow_fx",
            "state_path": "reports/shadow_state.json",
            "event_path": "reports/shadow_events.jsonl",
        }
        live_named_payload = {
            "version": "hungry_hippo_shapeshifter_v1",
            "deploy_reason": "design surface only",
        }
        live_scope_payload["unassigned_live_symbol_positions"] = [
            {
                "ticket": 45912807,
                "symbol": "BTCUSD",
                "magic": 0,
                "side": "BUY",
                "volume": 0.01,
                "price_open": 71585.66,
                "profit_usd": 290.73,
                "comment": "",
                "opened_at": "2026-04-12T04:51:38Z",
            }
        ]
        live_scope_payload["account_snapshot"] = {
            "collected_at": "2026-04-15T00:00:30+00:00",
            "equity_usd": 68534.02,
            "balance_usd": 68116.85,
            "profit_usd": 417.17,
            "position_count": 51,
        }

        with patch.object(board, "utc_now_iso", return_value="2026-04-15T00:01:00+00:00"):
            payload = board.build_payload(
                live_scope_payload,
                execution_monitor_payload,
                shadow_deploy_payload,
                live_named_payload,
            )

        self.assertEqual(payload["summary"]["live_lane_count"], 6)
        self.assertEqual(payload["summary"]["enabled_live_lane_count"], 5)
        self.assertEqual(payload["summary"]["disabled_live_lane_count"], 1)
        self.assertEqual(payload["summary"]["live_lanes_visible_now"], 3)
        self.assertEqual(payload["summary"]["live_lanes_flat_now"], 1)
        self.assertEqual(payload["summary"]["disabled_not_expected_now"], 1)
        self.assertEqual(payload["summary"]["live_lanes_with_state_mismatch"], 1)
        self.assertEqual(payload["summary"]["live_lanes_visible_but_runtime_stale"], 1)
        self.assertEqual(payload["summary"]["shadow_confusion_rows"], 1)
        self.assertEqual(payload["summary"]["scoped_live_positions_visible_now"], 7)
        self.assertEqual(payload["summary"]["legacy_outside_scope_positions_visible_now"], 3)
        self.assertEqual(payload["summary"]["unassigned_live_symbol_positions"], 1)
        self.assertEqual(payload["summary"]["detached_inventory_positions"], 4)
        self.assertEqual(payload["summary"]["detached_inventory_profit_usd"], 303.07)
        self.assertEqual(payload["summary"]["detached_inventory_live_pnl_share_pct"], 72.6)
        self.assertEqual(payload["summary"]["account_snapshot_freshness"], "fresh")
        self.assertEqual(payload["summary"]["recent_visibility_changes"], 0)
        self.assertEqual(payload["account_snapshot_freshness"]["age_seconds"], 30)

        live_rows = {row["lane"]: row for row in payload["live_rows"]}
        self.assertEqual(live_rows["live_alpha"]["mt5_visibility_status"], "visible_now")
        self.assertEqual(live_rows["live_beta"]["mt5_visibility_status"], "inactive_stale_managed_state")
        self.assertFalse(live_rows["live_beta"]["should_show_trades_in_mt5_now"])
        self.assertEqual(live_rows["live_gamma"]["mt5_visibility_status"], "live_but_flat_now")
        self.assertEqual(live_rows["live_delta"]["mt5_visibility_status"], "visible_now_with_legacy_inventory")
        self.assertEqual(live_rows["live_epsilon"]["mt5_visibility_status"], "visible_now_runtime_stale")
        self.assertTrue(live_rows["live_epsilon"]["should_show_trades_in_mt5_now"])
        self.assertEqual(live_rows["live_zeta"]["mt5_visibility_status"], "disabled_not_expected_in_mt5")
        self.assertFalse(live_rows["live_zeta"]["should_show_trades_in_mt5_now"])
        self.assertEqual(payload["unassigned_live_symbol_positions"][0]["symbol"], "BTCUSD")
        self.assertEqual(payload["account_snapshot"]["position_count"], 51)

        shadow_row = payload["shadow_confusion_rows"][0]
        self.assertEqual(shadow_row["mt5_visibility_status"], "shadow_only_not_expected_in_mt5")
        self.assertFalse(shadow_row["should_show_trades_in_mt5_now"])

    def test_render_markdown_mentions_shadow_confusion(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "summary": {
                "live_lane_count": 1,
                "enabled_live_lane_count": 1,
                "disabled_live_lane_count": 1,
                "live_lanes_visible_now": 1,
                "live_lanes_flat_now": 0,
                "disabled_not_expected_now": 1,
                "live_lanes_with_state_mismatch": 0,
                "live_lanes_visible_but_runtime_stale": 0,
                "shadow_confusion_rows": 1,
                "scoped_live_positions_visible_now": 2,
                "legacy_outside_scope_positions_visible_now": 0,
                "unassigned_live_symbol_positions": 1,
                "detached_inventory_positions": 1,
                "detached_inventory_profit_usd": 290.73,
                "detached_inventory_live_pnl_share_pct": 69.7,
                "account_snapshot_freshness": "stale",
            },
            "account_snapshot": {
                "collected_at": "2026-04-14T23:55:00+00:00",
                "equity_usd": 68534.02,
                "balance_usd": 68116.85,
                "profit_usd": 417.17,
                "position_count": 51,
            },
            "account_snapshot_freshness": {
                "collected_at": "2026-04-14T23:55:00+00:00",
                "age_seconds": 300,
                "status": "stale",
                "warning": "account snapshot is 300s old, above the 120s freshness threshold",
            },
            "leadership_read": ["one"],
            "live_rows": [
                {
                    "lane": "live_alpha",
                    "enabled": True,
                    "live_magic": 1001,
                    "scoped_symbols": ["EURUSD"],
                    "managed_open_count": 2,
                    "broker_scoped_open_count": 2,
                    "broker_total_open_count": 2,
                    "outside_scope_profit_usd": 0.0,
                    "mt5_visibility_status": "visible_now",
                    "should_show_trades_in_mt5_now": True,
                    "visibility_reason": "broker has positions",
                    "recommended_action": "none",
                    "notes": "healthy",
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "execution_status": "ok",
                    "last_state_write": "x",
                    "last_event": "y",
                    "last_seen": "z",
                }
                ,
                {
                    "lane": "live_disabled",
                    "enabled": False,
                    "pause_note": "manual_pause",
                    "live_magic": 1002,
                    "scoped_symbols": ["ETHUSD"],
                    "managed_open_count": 0,
                    "broker_scoped_open_count": 0,
                    "broker_total_open_count": 0,
                    "outside_scope_profit_usd": 0.0,
                    "mt5_visibility_status": "disabled_not_expected_in_mt5",
                    "should_show_trades_in_mt5_now": False,
                    "visibility_reason": "disabled lane",
                    "recommended_action": "none",
                    "notes": "paused",
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "execution_status": "paused",
                    "last_state_write": "",
                    "last_event": "",
                    "last_seen": "",
                }
            ],
            "unassigned_live_symbol_positions": [
                {
                    "ticket": 1,
                    "symbol": "BTCUSD",
                    "magic": 0,
                    "side": "BUY",
                    "volume": 0.01,
                    "price_open": 71585.66,
                    "profit_usd": 290.73,
                    "comment": "",
                    "opened_at": "2026-04-12T04:51:38Z",
                }
            ],
            "shadow_confusion_rows": [
                {
                    "lane": "shadow_lane",
                    "kind": "shadow_fx",
                    "visibility_reason": "shadow launch",
                    "state_path": "reports/x.json",
                    "event_path": "reports/x.jsonl",
                    "named_live_config_path": "configs/y.json",
                    "named_live_config_version": "v1",
                    "named_live_config_note": "misleading name",
                    "named_live_config_deploy_reason": "design only",
                }
            ],
        }

        markdown = board.render_markdown(payload)

        self.assertIn("MT5 User Visibility Board", markdown)
        self.assertIn("Pinned Account Snapshot", markdown)
        self.assertIn("Freshness: `stale`", markdown)
        self.assertIn("Freshness Warning", markdown)
        self.assertIn("Detached Inventory Still Moving MT5 Equity", markdown)
        self.assertIn("Live Lanes Visible In MT5 Now", markdown)
        self.assertIn("Paused Or Disabled Live IDs", markdown)
        self.assertIn("Unassigned Broker Positions On Live Symbols", markdown)
        self.assertIn("Shadow Confusion", markdown)
        self.assertIn("shadow_lane", markdown)
        self.assertIn("live_disabled", markdown)
        self.assertIn("detached_inventory_pnl", markdown)

    def test_build_payload_tracks_recent_visibility_changes(self) -> None:
        live_scope_payload = {
            "rows": [
                {
                    "lane": "live_eth_lane",
                    "kind": "live_crypto",
                    "enabled": False,
                    "pause_note": "paused_for_replacement",
                    "live_magic": 2001,
                    "scoped_symbols": ["ETHUSD"],
                    "managed_open_count": 0,
                    "broker_scoped_open_count": 0,
                    "broker_total_open_count": 0,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "aligned",
                    "recommended_action": "none",
                    "notes": "paused",
                }
            ],
            "account_snapshot": {
                "collected_at": "2026-04-15T00:00:30+00:00",
                "equity_usd": 1000.0,
                "balance_usd": 999.0,
                "profit_usd": 1.0,
                "position_count": 0,
            },
        }
        execution_monitor_payload = {"rows": []}
        shadow_deploy_payload = {"name": "shadow_lane", "kind": "shadow_fx", "state_path": "a", "event_path": "b"}
        live_named_payload = {"version": "v1", "deploy_reason": "design only"}
        previous_payload = {
            "live_rows": [
                {
                    "lane": "live_eth_lane",
                    "enabled": True,
                    "should_show_trades_in_mt5_now": False,
                    "mt5_visibility_status": "live_but_flat_now",
                }
            ]
        }

        with patch.object(board, "utc_now_iso", return_value="2026-04-15T00:01:00+00:00"):
            payload = board.build_payload(
                live_scope_payload,
                execution_monitor_payload,
                shadow_deploy_payload,
                live_named_payload,
                previous_payload=previous_payload,
            )

        self.assertEqual(payload["summary"]["recent_visibility_changes"], 1)
        self.assertEqual(payload["recent_visibility_changes"][0]["lane"], "live_eth_lane")
        self.assertEqual(payload["recent_visibility_changes"][0]["previous_status"], "live_but_flat_now")
        self.assertEqual(payload["recent_visibility_changes"][0]["current_status"], "disabled_not_expected_in_mt5")
        self.assertIn("Recent MT5 visibility changes detected", payload["leadership_read"][1])

    def test_refresh_live_scope_payload_if_needed_rebuilds_stale_source(self) -> None:
        stale_payload = {
            "account_snapshot": {
                "collected_at": "2026-04-15T00:00:00+00:00",
                "equity_usd": 100.0,
            }
        }
        refreshed_payload = {
            "account_snapshot": {
                "collected_at": "2026-04-15T00:05:00+00:00",
                "equity_usd": 101.0,
            }
        }

        with (
            patch.object(board.build_live_magic_scope_audit, "build_payload", return_value=refreshed_payload) as build_mock,
            patch.object(board.build_live_magic_scope_audit, "write_outputs") as write_mock,
            patch.object(board, "load_json", return_value=refreshed_payload),
        ):
            result = board.refresh_live_scope_payload_if_needed(
                stale_payload,
                generated_at="2026-04-15T00:05:30+00:00",
            )

        build_mock.assert_called_once()
        write_mock.assert_called_once_with(refreshed_payload)
        self.assertEqual(result["account_snapshot"]["equity_usd"], 101.0)

    def test_build_payload_falls_back_to_registry_pause_note(self) -> None:
        live_scope_payload = {
            "rows": [
                {
                    "lane": "live_disabled",
                    "kind": "live_fx",
                    "enabled": False,
                    "live_magic": 2001,
                    "scoped_symbols": ["ETHUSD"],
                    "managed_open_count": 0,
                    "broker_scoped_open_count": 0,
                    "broker_total_open_count": 0,
                    "outside_scope_open_count": 0,
                    "outside_scope_symbols": {},
                    "scope_status": "aligned",
                    "recommended_action": "none",
                    "notes": "",
                }
            ]
        }
        execution_monitor_payload = {"rows": []}
        shadow_deploy_payload = {
            "name": "shadow_lane",
            "kind": "shadow_fx",
            "state_path": "reports/shadow_state.json",
            "event_path": "reports/shadow_events.jsonl",
        }
        live_named_payload = {"version": "v1", "deploy_reason": "design only"}
        runner_registry_payload = {
            "lanes": [
                {
                    "name": "live_disabled",
                    "enabled": False,
                    "pause_note": "decommissioned_for_registry_reason",
                }
            ]
        }

        payload = board.build_payload(
            live_scope_payload,
            execution_monitor_payload,
            shadow_deploy_payload,
            live_named_payload,
            runner_registry_payload,
        )

        live_rows = {row["lane"]: row for row in payload["live_rows"]}
        self.assertEqual(live_rows["live_disabled"]["pause_note"], "decommissioned_for_registry_reason")


if __name__ == "__main__":
    unittest.main()
