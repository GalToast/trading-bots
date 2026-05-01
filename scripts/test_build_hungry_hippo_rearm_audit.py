#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_rearm_audit as audit


class HungryHippoRearmAuditTests(unittest.TestCase):
    def test_btc_xau_nzd_and_gbp_receive_expected_statuses(self) -> None:
        payload = audit.build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["BTCUSD"]["current_max_injections"], 0)
        self.assertEqual(rows["BTCUSD"]["hold_gate_status"], "aligned")
        self.assertEqual(rows["BTCUSD"]["overall_status"], "aligned")
        self.assertFalse(rows["NZDUSD"]["current_should_rearm_now"])
        self.assertEqual(rows["NZDUSD"]["current_max_injections"], 0)
        self.assertFalse(rows["XAUUSD"]["rearm_active_now"])
        self.assertEqual(rows["XAUUSD"]["session_status"], "aligned")
        self.assertEqual(rows["XAUUSD"]["overall_status"], "aligned")
        self.assertEqual(rows["GBPUSD"]["overall_status"], "aligned")


if __name__ == "__main__":
    unittest.main()
