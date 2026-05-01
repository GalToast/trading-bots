#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_proof_promotion_ladder as ladder


class CoinbaseIsolatedRunnerProofPromotionLadderTests(unittest.TestCase):
    def test_payload_emits_expected_verdict(self) -> None:
        payload = ladder.build_payload()

        self.assertEqual(payload["summary"]["verdict"], "no_lane_above_probe_only_yet")
        self.assertEqual(payload["summary"]["highest_current_stage"], "supervised_clean_waiting_for_signal")

    def test_stage_rules_cover_expected_progression(self) -> None:
        rules = {row["stage"]: row for row in ladder.build_stage_rules()}

        self.assertIn("dry_probe_passed", rules)
        self.assertIn("deployable_lane", rules)
        self.assertEqual(rules["deployable_lane"]["rank"], 5)

    def test_tru_and_sup_are_probe_only(self) -> None:
        rows = {row["coin"]: row for row in ladder.build_lane_rows()}

        self.assertEqual(rows["TRU-USD"]["current_stage"], "supervised_clean_waiting_for_signal")
        self.assertEqual(rows["SUP-USD"]["current_stage"], "supervised_clean_waiting_for_signal")

    def test_nom_and_bal_remain_blocked(self) -> None:
        rows = {row["coin"]: row for row in ladder.build_lane_rows()}

        self.assertEqual(rows["NOM-USD"]["current_stage"], "blocked_or_deferred")
        self.assertEqual(rows["BAL-USD"]["current_stage"], "blocked_or_deferred")


if __name__ == "__main__":
    unittest.main()
