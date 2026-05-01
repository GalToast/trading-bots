#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_sleeve_book_config as builder


class CoinbaseIsolatedRunnerSleeveBookConfigTests(unittest.TestCase):
    def test_build_payload_mixes_exact_and_inferred_rows_honestly(self) -> None:
        payload = builder.build_payload()
        rows = {row["coin"]: row for row in payload["configs"]}

        self.assertEqual(rows["NOM-USD"]["strategy"], "range_breakout")
        self.assertEqual(rows["NOM-USD"]["range_lookback"], 10)
        self.assertEqual(rows["TRU-USD"]["strategy"], "momentum")
        self.assertEqual(rows["TRU-USD"]["lookback"], 10)
        self.assertEqual(rows["A8-USD"]["config_status"], "inferred_from_runner_family_with_board_lookback")
        self.assertEqual(rows["CFG-USD"]["lookback"], 25)
        self.assertEqual(rows["RAVE-USD"]["config_status"], "inferred_family_baseline")


if __name__ == "__main__":
    unittest.main()
