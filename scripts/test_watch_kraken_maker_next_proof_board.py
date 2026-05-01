#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watch_kraken_maker_next_proof_board as watcher


class KrakenMakerNextProofWatcherTests(unittest.TestCase):
    def test_snapshot_extracts_primary_taker_guard(self) -> None:
        payload = {
            "summary": {
                "primary_lane": "parallel_ratio50_taker_guard",
                "primary_status": "collect_more",
                "next_action": "monitor_parallel_ratio50_taker_guard",
                "read": "test read",
            },
            "parallel_ratio50_taker_guard": {
                "status": "collect_more",
                "next_action": "monitor_parallel_ratio50_taker_guard",
                "closes": 4,
                "losses": 0,
                "ghost_marks": 16,
                "open_positions": 0,
                "max_concurrent_positions": 3,
                "realized_net_usd": 2.744209,
                "closes_remaining": 16,
                "ghost_marks_remaining": 4,
                "gate_reasons": ["needs_20_closes"],
            },
        }

        snapshot = watcher.snapshot_from_payload(payload)

        self.assertEqual(snapshot["primary_lane"], "parallel_ratio50_taker_guard")
        self.assertEqual(snapshot["primary_closes"], 4)
        self.assertEqual(snapshot["primary_losses"], 0)
        self.assertEqual(snapshot["primary_ghost_marks"], 16)
        self.assertEqual(snapshot["primary_closes_remaining"], 16)

    def test_snapshot_extracts_primary_live_exec_lane(self) -> None:
        payload = {
            "summary": {
                "primary_lane": "parallel_ratio50_taker_guard_live_exec",
                "primary_status": "collect_more",
                "next_action": "monitor_parallel_ratio50_taker_guard_live_exec",
                "read": "live exec read",
            },
            "parallel_ratio50_taker_guard": {
                "closes": 27,
                "losses": 1,
                "ghost_marks": 100,
                "open_positions": 1,
                "realized_net_usd": 12.643172,
            },
            "parallel_ratio50_taker_guard_live_exec": {
                "status": "collect_more",
                "next_action": "monitor_parallel_ratio50_taker_guard_live_exec",
                "closes": 12,
                "losses": 0,
                "ghost_marks": 43,
                "open_positions": 0,
                "max_concurrent_positions": 2,
                "realized_net_usd": 5.861446,
                "closes_remaining": 8,
                "ghost_marks_remaining": 0,
                "gate_reasons": ["needs_20_closes"],
            },
        }

        snapshot = watcher.snapshot_from_payload(payload)

        self.assertEqual(snapshot["primary_lane"], "parallel_ratio50_taker_guard_live_exec")
        self.assertEqual(snapshot["primary_closes"], 12)
        self.assertEqual(snapshot["primary_losses"], 0)
        self.assertEqual(snapshot["primary_open_positions"], 0)
        self.assertEqual(snapshot["primary_realized_net_usd"], 5.861446)

    def test_snapshot_extracts_primary_dds25_fixed_without_falling_back(self) -> None:
        payload = {
            "summary": {
                "primary_lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
                "primary_status": "collect_more",
                "next_action": "monitor_parallel_ratio50_taker_guard_live_exec_dds25_fixed",
                "read": "dds25 fixed read",
            },
            "parallel_ratio50_taker_guard": {
                "closes": 28,
                "losses": 2,
                "ghost_marks": 102,
                "open_positions": 0,
                "realized_net_usd": 12.591252,
            },
            "parallel_ratio50_taker_guard_live_exec_dds25_fixed": {
                "status": "collect_more",
                "next_action": "monitor_parallel_ratio50_taker_guard_live_exec_dds25_fixed",
                "closes": 8,
                "losses": 0,
                "ghost_marks": 29,
                "open_positions": 0,
                "max_concurrent_positions": 1,
                "realized_net_usd": 5.155467,
                "closes_remaining": 12,
                "ghost_marks_remaining": 0,
                "gate_reasons": ["needs_20_closes", "parallel_not_exercised"],
            },
        }

        snapshot = watcher.snapshot_from_payload(payload)

        self.assertEqual(snapshot["primary_lane"], "parallel_ratio50_taker_guard_live_exec_dds25_fixed")
        self.assertEqual(snapshot["primary_closes"], 8)
        self.assertEqual(snapshot["primary_losses"], 0)
        self.assertEqual(snapshot["primary_ghost_marks"], 29)
        self.assertEqual(snapshot["primary_closes_remaining"], 12)
        self.assertEqual(snapshot["primary_gate_reasons"], ["needs_20_closes", "parallel_not_exercised"])

    def test_diff_reports_close_loss_and_maturity_changes(self) -> None:
        previous = {
            "primary_lane": "parallel_ratio50_taker_guard",
            "primary_status": "collect_more",
            "next_action": "monitor",
            "primary_closes": 4,
            "primary_losses": 0,
            "primary_ghost_marks": 16,
            "primary_open_positions": 0,
            "primary_realized_net_usd": 2.744209,
            "primary_closes_remaining": 16,
            "primary_ghost_marks_remaining": 4,
        }
        current = {
            **previous,
            "primary_status": "ready_for_next_shadow_stage",
            "next_action": "choose_next",
            "primary_closes": 20,
            "primary_ghost_marks": 42,
            "primary_realized_net_usd": 8.0,
            "primary_closes_remaining": 0,
            "primary_ghost_marks_remaining": 0,
        }

        changes = watcher.diff_messages(previous, current)

        self.assertTrue(any("primary status" in change for change in changes))
        self.assertTrue(any("closes 4 -> 20" in change for change in changes))
        self.assertTrue(any("ghost marks 16 -> 42" in change for change in changes))
        self.assertTrue(watcher.reached_terminal_attention(current))

    def test_format_switchboard_message_summarizes_proof(self) -> None:
        snapshot = {
            "primary_lane": "parallel_ratio50_taker_guard",
            "primary_status": "collect_more",
            "primary_closes": 4,
            "primary_losses": 0,
            "primary_ghost_marks": 16,
            "primary_open_positions": 0,
            "primary_realized_net_usd": 2.744209,
            "primary_closes_remaining": 16,
            "primary_ghost_marks_remaining": 4,
            "next_action": "monitor",
        }

        message = watcher.format_switchboard_message(["closes 3 -> 4"], snapshot)

        self.assertIn("parallel_ratio50_taker_guard", message)
        self.assertIn("closes=4", message)
        self.assertIn("losses=0", message)
        self.assertIn("closes 3 -> 4", message)


if __name__ == "__main__":
    unittest.main()
