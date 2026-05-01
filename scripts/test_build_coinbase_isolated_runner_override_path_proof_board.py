#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_override_path_proof_board as board


class CoinbaseIsolatedRunnerOverridePathProofBoardTests(unittest.TestCase):
    def test_board_uses_longer_windows_for_tru_and_sup(self) -> None:
        payload = board.build_payload()
        rows = {row["coin"]: row for row in payload["rows"]}

        self.assertIn("TRU-USD", payload["summary"]["supervised_probe_targets"])
        self.assertIn("SUP-USD", payload["summary"]["supervised_probe_targets"])
        self.assertEqual(rows["TRU-USD"]["status"], "clean_across_multiple_windows_waiting_for_signal")
        self.assertEqual(rows["TRU-USD"]["supervised_probe_max_cycles"], 3)
        self.assertEqual(rows["SUP-USD"]["status"], "clean_across_multiple_windows_waiting_for_signal")
        self.assertEqual(rows["SUP-USD"]["supervised_probe_max_cycles"], 3)
        self.assertEqual(payload["summary"]["next_supervised_target"], "")
        self.assertEqual(payload["summary"]["deferred_next_target"], "NOM-USD")

    def test_board_defers_nom_and_blocks_bal(self) -> None:
        payload = board.build_payload()
        rows = {row["coin"]: row for row in payload["rows"]}

        self.assertEqual(rows["NOM-USD"]["status"], "dry_clean_defer_for_overlap")
        self.assertEqual(rows["BAL-USD"]["status"], "blocked_by_legacy_runtime")


if __name__ == "__main__":
    unittest.main()
