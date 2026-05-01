#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_atr_step_audit as audit


class HungryHippoAtrStepAuditTests(unittest.TestCase):
    def test_btc_eth_and_nas100_receive_expected_statuses(self) -> None:
        payload = audit.build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["BTCUSD"]["status"], "conflict")
        self.assertEqual(rows["ETHUSD"]["status"], "manual_review_required")
        self.assertEqual(rows["NAS100"]["status"], "manual_review_required")


if __name__ == "__main__":
    unittest.main()
