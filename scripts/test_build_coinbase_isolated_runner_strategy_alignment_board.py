#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_strategy_alignment_board as board


class CoinbaseIsolatedRunnerStrategyAlignmentBoardTests(unittest.TestCase):
    def test_alignment_board_detects_direct_probe_and_blocked_lanes(self) -> None:
        payload = board.build_payload()
        rows = {row["coin"]: row for row in payload["rows"]}

        self.assertEqual(rows["TRU-USD"]["smoke_admission"], "direct_sleeve_book_proof")
        self.assertEqual(rows["A8-USD"]["smoke_admission"], "family_probe_only")
        self.assertEqual(rows["CFG-USD"]["smoke_admission"], "family_probe_only")
        self.assertEqual(rows["NOM-USD"]["smoke_admission"], "do_not_count_as_sleeve_book_proof")
        self.assertEqual(rows["SUP-USD"]["smoke_admission"], "do_not_count_as_sleeve_book_proof")
        self.assertEqual(rows["BAL-USD"]["smoke_admission"], "do_not_count_as_sleeve_book_proof")
        self.assertEqual(rows["RAVE-USD"]["smoke_admission"], "do_not_count_as_sleeve_book_proof")


if __name__ == "__main__":
    unittest.main()
