#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watch_hungry_hippo_forward_shadow_watch_board as watcher


class HungryHippoForwardShadowWatchWatcherTests(unittest.TestCase):
    def test_snapshot_from_payload_extracts_runtime_state(self) -> None:
        payload = {
            "summary": {
                "watch_symbol_count": 1,
                "not_launched_symbols": ["USDCAD"],
                "waiting_first_open_symbols": [],
                "waiting_first_close_symbols": [],
                "proof_started_symbols": [],
                "stale_runtime_symbols": [],
            },
            "rows": [
                {
                    "symbol": "USDCAD",
                    "generalization_status": "ready_for_shadow_discussion",
                    "runtime_state": "not_launched_yet",
                }
            ],
        }

        snapshot = watcher.snapshot_from_payload(payload)

        self.assertEqual(snapshot["watch_symbols"], ["USDCAD"])
        self.assertEqual(snapshot["runtime_states"], {"USDCAD": "not_launched_yet"})
        self.assertEqual(snapshot["generalization_statuses"], {"USDCAD": "ready_for_shadow_discussion"})

    def test_diff_messages_reports_watch_set_and_state_changes(self) -> None:
        previous = {
            "watch_symbols": ["USDCAD"],
            "runtime_states": {"USDCAD": "not_launched_yet"},
            "proof_started_symbols": [],
            "stale_runtime_symbols": [],
        }
        current = {
            "watch_symbols": ["USDCAD", "USDCHF"],
            "runtime_states": {"USDCAD": "launched_waiting_first_open", "USDCHF": "not_launched_yet"},
            "proof_started_symbols": ["USDCAD"],
            "stale_runtime_symbols": ["USDCHF"],
        }

        changes = watcher.diff_messages(previous, current)

        self.assertIn("watch_symbols ['USDCAD'] -> ['USDCAD', 'USDCHF']", changes)
        self.assertIn("USDCAD runtime_state not_launched_yet -> launched_waiting_first_open", changes)
        self.assertIn("USDCHF runtime_state missing -> not_launched_yet", changes)
        self.assertIn("proof_started_symbols [] -> ['USDCAD']", changes)
        self.assertIn("stale_runtime_symbols [] -> ['USDCHF']", changes)

    def test_proof_arrived_requires_started_symbol(self) -> None:
        self.assertFalse(watcher.proof_arrived({"proof_started_symbols": []}))
        self.assertTrue(watcher.proof_arrived({"proof_started_symbols": ["USDCAD"]}))

    def test_format_switchboard_message_includes_summary(self) -> None:
        content = watcher.format_switchboard_message(
            ["USDCAD runtime_state not_launched_yet -> launched_waiting_first_open"],
            {
                "watch_symbols": ["USDCAD"],
                "proof_started_symbols": [],
                "not_launched_symbols": [],
                "waiting_first_open_symbols": ["USDCAD"],
                "waiting_first_close_symbols": [],
                "stale_runtime_symbols": [],
            },
        )

        self.assertIn("watch_symbols=['USDCAD']", content)
        self.assertIn("waiting_first_open=['USDCAD']", content)
        self.assertIn("Changes: USDCAD runtime_state not_launched_yet -> launched_waiting_first_open", content)


if __name__ == "__main__":
    unittest.main()
