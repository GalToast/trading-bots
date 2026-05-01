#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_promotion_queue as queue_builder


class HungryHippoPromotionQueueTests(unittest.TestCase):
    def test_queue_assigns_expected_actions(self) -> None:
        payload = queue_builder.build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["GBPUSD"]["next_action"], "reconcile_live_path")
        self.assertEqual(rows["BTCUSD"]["next_action"], "hold_until_buy_realign")
        self.assertEqual(rows["NAS100"]["next_action"], "wait_for_session_window")
        self.assertEqual(rows["SOLUSD"]["next_action"], "add_canonical_coverage")


if __name__ == "__main__":
    unittest.main()
