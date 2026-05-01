#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
OPERATORS_DIR = SCRIPTS_DIR / "operators"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(OPERATORS_DIR) not in sys.path:
    sys.path.insert(0, str(OPERATORS_DIR))

import run_detached_inventory_action as runner


class RunDetachedInventoryActionTests(unittest.TestCase):
    def test_select_action_item_and_command_argv(self) -> None:
        payload = {
            "action_items": [
                {
                    "item": "unassigned_live_symbol_inventory",
                    "dry_run_argv": [
                        "python",
                        "scripts/operators/mt5_close_filtered.py",
                        "--ticket",
                        "45912807",
                        "--expect-count",
                        "1",
                    ],
                    "apply_argv": [
                        "python",
                        "scripts/operators/mt5_close_filtered.py",
                        "--ticket",
                        "45912807",
                        "--expect-count",
                        "1",
                        "--apply",
                    ],
                }
            ]
        }

        item = runner.select_action_item(payload, "unassigned_live_symbol_inventory")
        self.assertIsNotNone(item)
        self.assertEqual(
            runner.command_argv_for_item(item, apply=False),
            ["python", "scripts/operators/mt5_close_filtered.py", "--ticket", "45912807", "--expect-count", "1"],
        )
        self.assertEqual(
            runner.command_argv_for_item(item, apply=True),
            ["python", "scripts/operators/mt5_close_filtered.py", "--ticket", "45912807", "--expect-count", "1", "--apply"],
        )

    def test_refresh_commands_reads_payload_list(self) -> None:
        payload = {
            "refresh_commands": [
                "python scripts/build_live_magic_scope_audit.py",
                "python scripts/build_detached_inventory_action_board.py",
            ]
        }
        self.assertEqual(
            runner.refresh_commands(payload),
            [
                "python scripts/build_live_magic_scope_audit.py",
                "python scripts/build_detached_inventory_action_board.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()
