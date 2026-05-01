#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_coinbase_isolated_runner_multicoin_restart_drill as drill


class CoinbaseIsolatedRunnerMulticoinRestartDrillTests(unittest.TestCase):
    def test_multicoin_restart_drill_preserves_recovered_active_positions(self) -> None:
        payload = drill.build_payload()

        self.assertEqual(payload["continuity"]["verdict"], "continuity_pass")
        self.assertEqual(payload["continuity"]["replay_exit_coins"], [])
        self.assertEqual(payload["continuity"]["replay_close_coins"], [])
        self.assertEqual(payload["continuity"]["hold_jump_coins"], [])
        for coin in drill.DRILL_COINS:
            self.assertEqual(payload["first_run"]["coins"][coin]["position"], "active")
            self.assertEqual(payload["second_run"]["coins"][coin]["position"], "active")


if __name__ == "__main__":
    unittest.main()
