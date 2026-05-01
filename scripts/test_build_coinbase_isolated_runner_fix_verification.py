#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_fix_verification as verification


class CoinbaseIsolatedRunnerFixVerificationTests(unittest.TestCase):
    def test_verification_detects_current_fix_mix(self) -> None:
        payload = verification.build_payload()
        rows = {row["fix_type"]: row for row in payload["rows"]}

        self.assertEqual(payload["restart_drill_verdict"], "continuity_pass")
        self.assertEqual(payload["multicoin_restart_drill_verdict"], "continuity_pass")
        self.assertEqual(payload["verification_verdict"], "restart_drill_verified_for_controlled_smoke")
        self.assertEqual(rows["restart_rebuild"]["status"], "resolved")
        self.assertEqual(rows["ops_cli_controls"]["status"], "resolved")
        self.assertEqual(rows["session_gate_enforcement"]["status"], "resolved")
        self.assertEqual(rows["recovery_state_restore"]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
