#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_book_governance_board as board


class CoinbaseIsolatedRunnerBookGovernanceBoardTests(unittest.TestCase):
    def test_board_tracks_scope_conflict_and_saved_fibonacci_artifact(self) -> None:
        payload = board.build_payload()
        rows = {row["subject"]: row for row in payload["rows"]}

        self.assertEqual(rows["supertrend_deploy_now_claim"]["status"], "scope_conflict_needs_governance")
        self.assertEqual(rows["fibonacci_breakout_deploy_now_claim"]["status"], "saved_validation_exists_but_not_router_governed")
        self.assertIn("TRU-USD momentum_registry_validation", payload["summary"]["first_exact_smoke"])

    def test_board_keeps_runner_readiness_separate_from_book_rewrite(self) -> None:
        payload = board.build_payload()
        rows = {row["subject"]: row for row in payload["rows"]}

        self.assertEqual(rows["isolated_runner_operational_readiness"]["status"], "controlled_smoke_ready_only")
        self.assertEqual(rows["full_deploy_now_command"]["decision"], "do_not_treat_default_runner_command_as_board_approved_deployment")


if __name__ == "__main__":
    unittest.main()
