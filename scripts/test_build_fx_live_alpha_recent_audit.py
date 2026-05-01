#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_fx_live_alpha_recent_audit as audit


class BuildFxLiveAlphaRecentAuditTests(unittest.TestCase):
    def test_parse_recent_windows_splits_restart_segments(self) -> None:
        rows = [
            {"action": "fresh_start_prime", "ts_utc": "2026-04-13T18:00:00+00:00", "raw_close_alpha": 0.0, "raw_rearm_cooldown_bars": 0, "symbols": ["EURUSD", "GBPUSD"]},
            {"action": "close_ticket", "ts_utc": "2026-04-13T18:10:00+00:00", "symbol": "EURUSD", "realized_pnl": 1.2},
            {"action": "fresh_start_prime", "ts_utc": "2026-04-13T20:00:00+00:00", "raw_close_alpha": 1.0, "raw_rearm_cooldown_bars": 12, "symbols": ["EURUSD", "GBPUSD"]},
            {"action": "close_ticket", "ts_utc": "2026-04-13T20:20:00+00:00", "symbol": "GBPUSD", "realized_pnl": -0.05, "close_alpha": 1.0},
            {"action": "close_ticket", "ts_utc": "2026-04-13T20:20:22+00:00", "symbol": "GBPUSD", "realized_pnl": -0.10, "close_alpha": 1.0},
            {"action": "close_ticket", "ts_utc": "2026-04-13T20:42:37+00:00", "symbol": "GBPUSD", "realized_pnl": -0.12, "close_alpha": 1.0},
            {"action": "fresh_start_prime", "ts_utc": "2026-04-13T21:08:25+00:00", "raw_close_alpha": 0.5, "raw_rearm_cooldown_bars": 12, "symbols": ["EURUSD", "GBPUSD"]},
        ]

        windows = audit.parse_recent_windows(rows)

        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[1]["close_count"], 3)
        self.assertAlmostEqual(windows[1]["close_net_usd"], -0.27, places=6)
        self.assertEqual(windows[1]["symbol_breakdown_text"], "GBPUSD 3c / $-0.27")
        self.assertEqual(windows[2]["sample_status"], "thin_sample")

    def test_build_summary_flags_provisional_revert_and_momentum_split(self) -> None:
        live_state = {
            "metadata": {
                "raw_close_alpha": 0.5,
                "raw_rearm_cooldown_bars": 12,
                "symbols": ["EURUSD", "GBPUSD"],
            },
            "runner": {"pid": 41320, "started_at": "2026-04-13T21:08:25+00:00"},
            "symbols": {
                "EURUSD": {"realized_closes": 48, "realized_net_usd": 52.89, "open_tickets": [1, 2]},
                "GBPUSD": {"realized_closes": 94, "realized_net_usd": 148.55, "open_tickets": [1, 2, 3, 4]},
            },
        }
        momentum_state = {
            "metadata": {
                "raw_close_alpha": 0.0,
                "raw_rearm_cooldown_bars": 0,
            }
        }
        registry = {
            "lanes": [
                {
                    "name": "live_rearm_941777",
                    "restart_args": ["--raw-close-alpha", "0.5", "--raw-rearm-cooldown-bars", "12"],
                },
                {
                    "name": "live_momentum_alpha50_941778",
                    "restart_args": ["--raw-close-alpha", "1.0", "--raw-rearm-cooldown-bars", "12"],
                },
            ]
        }
        windows = [
            {"raw_close_alpha": 1.0, "close_count": 3, "close_net_usd": -0.27, "symbol_breakdown_text": "GBPUSD 3c / $-0.27"},
            {"raw_close_alpha": 0.5, "close_count": 0, "close_net_usd": 0.0, "sample_status": "thin_sample"},
        ]

        summary = audit.build_summary(live_state, momentum_state, registry, windows)

        self.assertTrue(summary["revert_is_thin_sample"])
        self.assertEqual(summary["next_gate"], "accumulate_post_revert_sample")
        self.assertEqual(summary["current_running_open_total"], 6)
        self.assertEqual(summary["current_running_realized_closes"], 142)
        self.assertTrue(summary["momentum_restart_needed"])

    def test_recent_windows_for_live_state_anchors_current_window_on_runner_start(self) -> None:
        rows = [
            {"action": "fresh_start_prime", "ts_utc": "2026-04-13T18:00:00+00:00", "raw_close_alpha": 0.0, "raw_rearm_cooldown_bars": 0, "symbols": ["EURUSD", "GBPUSD"]},
            {"action": "fresh_start_prime", "ts_utc": "2026-04-13T19:45:00+00:00", "raw_close_alpha": 1.0, "raw_rearm_cooldown_bars": 12, "symbols": ["EURUSD", "GBPUSD"]},
            {"action": "close_ticket", "ts_utc": "2026-04-13T20:20:00+00:00", "symbol": "GBPUSD", "realized_pnl": -0.05, "close_alpha": 1.0},
            {"action": "close_ticket", "ts_utc": "2026-04-13T20:20:22+00:00", "symbol": "GBPUSD", "realized_pnl": -0.10, "close_alpha": 1.0},
            {"action": "close_ticket", "ts_utc": "2026-04-13T20:42:37+00:00", "symbol": "GBPUSD", "realized_pnl": -0.12, "close_alpha": 1.0},
        ]
        live_state = {
            "runner": {"started_at": "2026-04-13T21:08:25+00:00"},
            "metadata": {"raw_close_alpha": 0.5, "raw_rearm_cooldown_bars": 12, "symbols": ["EURUSD", "GBPUSD"]},
        }

        windows = audit.recent_windows_for_live_state(rows, live_state)

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["raw_close_alpha"], 1.0)
        self.assertEqual(windows[0]["close_count"], 3)
        self.assertAlmostEqual(windows[0]["close_net_usd"], -0.27, places=6)
        self.assertEqual(windows[-1]["raw_close_alpha"], 0.5)
        self.assertEqual(windows[-1]["close_count"], 0)


if __name__ == "__main__":
    unittest.main()
