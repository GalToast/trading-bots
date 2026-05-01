#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_coinbase_isolated_runner_restart_drill as drill


class CoinbaseIsolatedRunnerRestartDrillTests(unittest.TestCase):
    def test_restart_drill_preserves_recovered_active_position(self) -> None:
        payload = drill.build_payload()

        self.assertEqual(payload["first_run"]["position"], "active")
        self.assertEqual(payload["second_run"]["position"], "active")
        self.assertEqual(payload["continuity"]["verdict"], "continuity_pass")
        self.assertFalse(payload["continuity"]["replay_exit_detected"])
        self.assertFalse(payload["continuity"]["replay_close_detected"])


if __name__ == "__main__":
    unittest.main()
