#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import track_m5_live_portfolio as tracker


class LiveM5PortfolioTrackerTests(unittest.TestCase):
    def test_runtime_mismatch_fields_flags_timeframe_and_poll_drift(self) -> None:
        self.assertEqual(
            tracker.runtime_mismatch_fields(
                configured_timeframe="M5",
                configured_poll_seconds=1.0,
                configured_step=5.0,
                runtime_timeframe="H1",
                runtime_poll_seconds=60.0,
                runtime_step=5.0,
            ),
            ["timeframe", "poll_seconds"],
        )

    def test_build_payload_from_inputs_aggregates_and_flags_review_items(self) -> None:
        root = tracker.ROOT
        btc_state = root / "tmp_btc_state.json"
        eth_state = root / "tmp_eth_state.json"
        sol_state = root / "tmp_sol_state.json"
        gbp_state = root / "tmp_gbp_state.json"
        try:
            btc_state.write_text(
                """{
  "metadata": {"live_magic": 941780, "timeframe": "M5", "step": 100},
  "runner": {"pid": 10, "heartbeat_at": "2026-04-14T16:49:00+00:00", "poll_seconds": 1},
  "symbols": {"BTCUSD": {"timeframe": "M5", "open_tickets": [{"direction": "SELL"}, {"direction": "BUY"}], "anchor_resets": 0, "rearm_opens": 0, "realized_closes": 4, "realized_net_usd": 100, "next_buy_level": 74580.76, "next_sell_level": 74780.76}}
}""",
                encoding="utf-8",
            )
            eth_state.write_text(
                """{
  "metadata": {"live_magic": 941784, "timeframe": "H1", "step": 5},
  "runner": {"pid": 20, "heartbeat_at": "2026-04-14T16:49:00+00:00", "poll_seconds": 60},
  "symbols": {"ETHUSD": {"timeframe": "H1", "open_tickets": [{"direction": "BUY"}, {"direction": "BUY"}, {"direction": "BUY"}], "anchor_resets": 0, "rearm_opens": 0, "realized_closes": 3, "realized_net_usd": -14.61, "next_buy_level": 2346.79, "next_sell_level": 2366.79}}
}""",
                encoding="utf-8",
            )
            sol_state.write_text(
                """{
  "metadata": {"live_magic": 941783, "timeframe": "M5", "step": 0.12},
  "runner": {"pid": 30, "heartbeat_at": "2026-04-14T16:49:00+00:00", "poll_seconds": 1},
  "symbols": {"SOLUSD": {"timeframe": "M5", "open_tickets": [{"direction": "BUY"}], "anchor_resets": 0, "rearm_opens": 0, "realized_closes": 1, "realized_net_usd": -2.5, "next_buy_level": 85.5, "next_sell_level": 85.98}}
}""",
                encoding="utf-8",
            )
            gbp_state.write_text(
                """{
  "metadata": {"timeframe": "M5", "step": 0.00034},
  "runner": {"pid": 40, "heartbeat_at": "2026-04-14T16:49:00+00:00", "poll_seconds": 30},
  "symbols": {"GBPUSD": {"timeframe": "M5", "open_tickets": [{"direction": "SELL"}], "anchor_resets": 0, "rearm_opens": 0, "realized_closes": 6, "realized_net_usd": 3.74, "next_buy_level": 1.35627, "next_sell_level": 1.35695}}
}""",
                encoding="utf-8",
            )

            payload = tracker.build_payload_from_inputs(
                generated_at="2026-04-14T16:50:00+00:00",
                registry_rows=[
                    {
                        "name": "live_btcusd_m5_warp_probation_941780",
                        "state_path": str(btc_state.relative_to(root)),
                        "restart_args": ["x", "--timeframe", "M5", "--step", "100", "--poll-seconds", "1"],
                    },
                    {
                        "name": "live_ethusd_m5_warp_941784",
                        "state_path": str(eth_state.relative_to(root)),
                        "restart_args": ["x", "--timeframe", "M5", "--step", "5", "--poll-seconds", "1"],
                    },
                    {
                        "name": "live_solusd_m5_warp_941783",
                        "enabled": False,
                        "pause_note": "paused_for_test",
                        "state_path": str(sol_state.relative_to(root)),
                        "restart_args": ["x", "--timeframe", "M5", "--step", "0.12", "--poll-seconds", "1"],
                    },
                ],
                expansion_registry_rows=[
                    {
                        "name": "shadow_gbpusd_m5_warp",
                        "kind": "shadow_fx",
                        "state_path": str(gbp_state.relative_to(root)),
                        "restart_args": ["x", "--timeframe", "M5", "--step", "0.00034", "--poll-seconds", "30"],
                    }
                ],
                execution_rows={
                    "live_btcusd_m5_warp_probation_941780": {
                        "watchdog_status": "ok",
                        "last_trade_event_at": "2026-04-14T16:40:00+00:00",
                        "clean_forward_realized_delta_usd": 0.0,
                        "clean_forward_new_closes": 0,
                        "broker_sync_inherited_closes": 0,
                        "broker_sync_inherited_realized_usd": 0.0,
                        "quote_bid": 75158.23,
                        "quote_ask": 75345.79,
                        "notes": "close_event_gap=2",
                    },
                    "live_ethusd_m5_warp_941784": {
                        "watchdog_status": "",
                        "last_trade_event_at": "",
                        "clean_forward_realized_delta_usd": 0.0,
                        "clean_forward_new_closes": 0,
                        "broker_sync_inherited_closes": 3,
                        "broker_sync_inherited_realized_usd": -14.61,
                        "quote_bid": 2346.91,
                        "quote_ask": 2352.69,
                        "notes": "broker_sync_inherited_closes=3/-14.61",
                    },
                    "live_solusd_m5_warp_941783": {
                        "watchdog_status": "",
                        "last_trade_event_at": "",
                        "clean_forward_realized_delta_usd": 0.0,
                        "clean_forward_new_closes": 0,
                        "broker_sync_inherited_closes": 1,
                        "broker_sync_inherited_realized_usd": -2.5,
                        "quote_bid": 85.44,
                        "quote_ask": 85.66,
                        "notes": "broker_sync_inherited_closes=1/-2.50",
                    },
                    "shadow_gbpusd_m5_warp": {
                        "watchdog_status": "ok",
                        "last_trade_event_at": "2026-04-14T16:49:45+00:00",
                        "clean_forward_realized_delta_usd": 0.0,
                        "clean_forward_new_closes": 0,
                        "broker_sync_inherited_closes": 0,
                        "broker_sync_inherited_realized_usd": 0.0,
                        "quote_bid": 1.35661,
                        "quote_ask": 1.35677,
                        "notes": "forward=bootstrap_positive closes=6",
                    },
                },
                broker_connected=True,
                account_payload={
                    "equity_usd": 1000.0,
                    "balance_usd": 1200.0,
                    "profit_usd": -200.0,
                    "margin_level_pct": 900.0,
                },
                broker_by_magic={
                    941780: {"open_count": 2, "buy_count": 1, "sell_count": 1, "floating_usd": -8100.0},
                    941784: {"open_count": 3, "buy_count": 3, "sell_count": 0, "floating_usd": -30.0},
                    941783: {"open_count": 1, "buy_count": 1, "sell_count": 0, "floating_usd": 5.0},
                },
                btc_concentration_summary={
                    "combined_floating_usd": -7735.14,
                    "combined_net_usd": -5219.37,
                    "triggered_thresholds": ["m5_no_compression"],
                },
                ghost_positions_payload={
                    "ts_utc": "2026-04-14T16:50:00+00:00",
                    "positions": [
                        {"ticket": 101, "symbol": "BTCUSD", "magic": 941780, "profit": -44.4, "status": "PAUSED"},
                        {"ticket": 102, "symbol": "SOLUSD", "magic": 941783, "profit": -4.2, "status": "PAUSED"},
                    ],
                },
            )

            summary = payload["summary"]
            self.assertEqual(summary["combined_realized_usd"], 82.89)
            self.assertEqual(summary["combined_floating_usd"], -8125.0)
            self.assertEqual(summary["combined_net_usd"], -8042.11)
            self.assertEqual(summary["combined_managed_open_count"], 6)
            self.assertEqual(summary["combined_broker_open_count"], 6)
            self.assertEqual(summary["operator_posture"], "operator_review_required")

            flag_names = {row["flag"] for row in payload["flags"]}
            self.assertIn("combined_floating_pressure", flag_names)
            self.assertIn("combined_floating_pct_pressure", flag_names)
            self.assertIn("watchdog_surface_gap", flag_names)
            self.assertIn("runtime_config_drift", flag_names)
            self.assertIn("detached_broker_inventory", flag_names)
            self.assertNotIn("new_lane_early_negative", flag_names)
            self.assertIn("btc_concentration_triggers", flag_names)

            eth_row = next(row for row in payload["rows"] if row["lane"] == "live_ethusd_m5_warp_941784")
            self.assertEqual(eth_row["runtime_mismatch_fields"], ["timeframe", "poll_seconds"])
            sol_row = next(row for row in payload["rows"] if row["lane"] == "live_solusd_m5_warp_941783")
            self.assertFalse(sol_row["enabled"])
            self.assertEqual(sol_row["watchdog_status"], "paused")
            self.assertEqual(sol_row["pid"], 0)
            self.assertTrue(sol_row["detached_broker_inventory"])
            expansion_row = next(row for row in payload["expansion_watch_rows"] if row["lane"] == "shadow_gbpusd_m5_warp")
            self.assertEqual(expansion_row["kind"], "shadow_fx")
            self.assertEqual(expansion_row["realized_closes"], 6)
            self.assertIsNone(expansion_row["floating_usd"])
            rendered = tracker.render_markdown(payload)
            self.assertIn("## Expansion Watch", rendered)
            self.assertIn("shadow_gbpusd_m5_warp", rendered)
            self.assertIn("paused_for_test", rendered)
            self.assertIn("broker inventory still open while lane is paused/stale", rendered)
            self.assertIn("## Ghost Carry Audit", rendered)
            self.assertIn("- Live reconciliation: active `2` / stale-or-cleared `0`", rendered)
            self.assertIn("| live_btcusd_m5_warp_probation_941780 | BTCUSD | 941780 | PAUSED | active | 1 | $-44.40 | 101 |", rendered)
            self.assertIn("| live_solusd_m5_warp_941783 | SOLUSD | 941783 | PAUSED | active | 1 | $-4.20 | 102 |", rendered)
            self.assertIn("| live_solusd_m5_warp_941783 |", rendered)
            self.assertIn("| live_solusd_m5_warp_941783 | $-2.50 | $+5.00 | $+2.50 | 1 | 1/1 |", rendered)
        finally:
            for path in (btc_state, eth_state, sol_state, gbp_state):
                if path.exists():
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
