#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_live_btcusd_concentration_board as board


class BuildLiveBTCUSDConcentrationBoardTests(unittest.TestCase):
    def test_collect_unassigned_btc_positions_uses_active_magics_only(self) -> None:
        class DummyMT5:
            def positions_get(self, symbol: str | None = None):
                return [
                    SimpleNamespace(ticket=1, magic=941779, type=0, volume=0.01, price_open=70000.0, profit=10.0, comment="active", time=1710000000),
                    SimpleNamespace(ticket=2, magic=941780, type=0, volume=0.01, price_open=70100.0, profit=-5.0, comment="paused", time=1710000300),
                    SimpleNamespace(ticket=3, magic=0, type=1, volume=0.02, price_open=70200.0, profit=7.0, comment="manual", time=1710000600),
                ]

            def shutdown(self) -> None:
                return None

        with patch.object(board.mt5_terminal_guard, "initialize_mt5", return_value=(True, {})):
            rows = board.collect_unassigned_btc_positions({941779, 941781}, mt5_module=DummyMT5())

        self.assertEqual([row["ticket"] for row in rows], [2, 3])

    def test_build_payload_excludes_paused_lane_from_active_totals(self) -> None:
        organism = {
            "live_lanes": [
                {"lane": "live_btcusd_exc2_tight_941779", "realized_usd": 100.0, "floating_usd": -10.0, "net_usd": 90.0, "open_count": 1, "closes": 5, "watchdog_status": "ok"},
                {"lane": "live_btcusd_m15_warp_941781", "realized_usd": 200.0, "floating_usd": 0.0, "net_usd": 200.0, "open_count": 0, "closes": 7, "watchdog_status": "ok"},
                {"lane": "live_btcusd_m5_warp_probation_941780", "realized_usd": 999.0, "floating_usd": -99.0, "net_usd": 900.0, "open_count": 9, "closes": 11, "watchdog_status": "paused"},
            ]
        }
        execution = {"rows": []}
        registry = {
            "lanes": [
                {"name": "live_btcusd_exc2_tight_941779", "enabled": True},
                {"name": "live_btcusd_m15_warp_941781", "enabled": True},
                {"name": "live_btcusd_m5_warp_probation_941780", "enabled": False, "pause_note": "paused_for_test"},
            ]
        }
        survivability = {
            "current_bid": 75200.0,
            "current_ask": 75210.0,
            "account": {"equity": 10000.0, "balance": 9900.0, "margin_level": 1000.0},
        }

        with (
            patch.object(board, "load_json", side_effect=[organism, execution, registry]),
            patch.object(board, "ensure_fresh_survivability_payload", side_effect=AssertionError("should not refresh survivability for paused M5 lane")),
            patch.object(board, "collect_btc_market_snapshot", return_value={"current_bid": 75200.0, "current_ask": 75210.0, "equity": 10000.0, "balance": 9900.0, "margin_level": 1000.0}),
            patch.object(board, "collect_unassigned_btc_positions", return_value=[]),
            patch.object(board, "utc_now_iso", return_value="2026-04-15T23:02:00+00:00"),
        ):
            payload = board.build_payload()

        self.assertEqual(payload["summary"]["active_btc_lane_count"], 2)
        self.assertEqual(payload["summary"]["paused_btc_lane_count"], 1)
        self.assertEqual(payload["summary"]["combined_net_usd"], 290.0)
        self.assertIsNone(payload["survivability_source_age_seconds"])
        self.assertEqual({row["lane"] for row in payload["rows"]}, {"live_btcusd_exc2_tight_941779", "live_btcusd_m15_warp_941781"})
        self.assertEqual(payload["paused_rows"][0]["lane"], "live_btcusd_m5_warp_probation_941780")
        self.assertEqual(payload["paused_rows"][0]["pause_note"], "paused_for_test")


if __name__ == "__main__":
    unittest.main()
