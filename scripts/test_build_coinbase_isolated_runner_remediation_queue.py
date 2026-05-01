#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_remediation_queue as queue


class CoinbaseIsolatedRunnerRemediationQueueTests(unittest.TestCase):
    def test_queue_orders_recovery_blockers_first(self) -> None:
        rows = queue.build_rows()

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["fix_type"], "recovery_state_restore")
        self.assertEqual(rows[1]["fix_type"], "deterministic_restart_rebuild")

    def test_ops_and_session_follow_recovery(self) -> None:
        rows = queue.build_rows()

        self.assertEqual(rows[2]["fix_type"], "ops_cli_controls")
        self.assertEqual(rows[3]["fix_type"], "session_gate_enforcement")


if __name__ == "__main__":
    unittest.main()
