#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pocket_executor
from build_pocket_executor_review import summarize
from pocket_executor import PocketExecutor


class PocketExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._events = []
        self._original_append = pocket_executor.append_jsonl
        pocket_executor.append_jsonl = lambda _path, record: self._events.append(record)

    def tearDown(self) -> None:
        pocket_executor.append_jsonl = self._original_append

    def test_target_exit_requires_fee_paid_net_profit(self) -> None:
        executor = PocketExecutor(starting_cash=100.0, deploy_pct=0.8, fee_bps=120.0, target_net_pct=0.5)
        pocket = {
            "product_id": "TREE-USD",
            "variant_id": 127,
            "trigger": "inside_bar_break",
            "avg_net_pct": 0.894869,
            "win_rate_pct": 60.0,
            "worst_net_pct": -4.037363,
            "pocket_score": 6.651004,
        }

        executor.open_position(pocket, ask_price=0.0882, bid_price=0.0881)
        executor.check_exit(pocket, current_price=0.0899)

        self.assertIsNotNone(executor.position)
        self.assertFalse(any(row.get("action") == "shadow_close" for row in self._events))

    def test_entry_rejects_live_spread_trap(self) -> None:
        executor = PocketExecutor(starting_cash=100.0, deploy_pct=0.8, fee_bps=120.0, max_entry_spread_bps=10.0)
        pocket = {
            "product_id": "TREE-USD",
            "variant_id": 127,
            "trigger": "inside_bar_break",
        }

        executor.open_position(pocket, ask_price=0.0882, bid_price=0.0870)

        self.assertIsNone(executor.position)
        self.assertEqual(self._events[-1]["action"], "shadow_reject")
        self.assertEqual(self._events[-1]["reject_reason"], "entry_spread_too_wide")


class PocketExecutorReviewTests(unittest.TestCase):
    def test_summarize_reports_consecutive_profit_runs(self) -> None:
        events = [
            {"action": "shadow_close", "ts_utc": "1", "net": 1.0, "net_pct": 1.0, "entry_fee": 0.1, "exit_fee": 0.1},
            {"action": "shadow_close", "ts_utc": "2", "net": 2.0, "net_pct": 2.0, "entry_fee": 0.1, "exit_fee": 0.1},
            {"action": "shadow_close", "ts_utc": "3", "net": 3.0, "net_pct": 3.0, "entry_fee": 0.1, "exit_fee": 0.1},
            {"action": "shadow_close", "ts_utc": "4", "net": -1.0, "net_pct": -1.0, "entry_fee": 0.1, "exit_fee": 0.1},
        ]

        payload = summarize(events)

        self.assertEqual(payload["wins"], 3)
        self.assertEqual(payload["max_profitable_run"], 3)
        self.assertEqual(payload["current_loss_run"], 1)
        self.assertEqual(payload["verdict"], "consecutive_profit_proof")


if __name__ == "__main__":
    unittest.main()
