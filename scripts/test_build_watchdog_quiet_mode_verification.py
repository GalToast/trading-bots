#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_watchdog_quiet_mode_verification as verify


class WatchdogQuietModeVerificationTests(unittest.TestCase):
    def test_payload_reports_quiet_mode_verified(self) -> None:
        payload = verify.build_payload()

        self.assertEqual(payload["verdict"], "quiet_mode_verified")
        self.assertEqual(payload["summary"]["missing_tasks"], [])
        self.assertEqual(payload["summary"]["non_hidden_tasks"], [])

    def test_expected_tasks_are_all_hidden(self) -> None:
        payload = verify.build_payload()
        rows = {row["TaskName"]: row for row in payload["task_rows"]}

        for task_name in verify.EXPECTED_TASKS:
            self.assertIn(task_name, rows)
            self.assertTrue(rows[task_name]["Hidden"])

    def test_guard_scripts_use_hidden_start_process_only(self) -> None:
        payload = verify.build_payload()

        for row in payload["guard_rows"]:
            self.assertTrue(row["exists"])
            self.assertTrue(row["uses_hidden_start_process"])
            self.assertFalse(row["uses_direct_ensure_invocation"])


if __name__ == "__main__":
    unittest.main()
