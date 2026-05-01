#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runtime_launch_manifest as manifest


class CoinbaseIsolatedRuntimeLaunchManifestTests(unittest.TestCase):
    def test_manifest_covers_all_runtime_proof_rows(self) -> None:
        rows = manifest.build_rows()

        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["coin"], "A8-USD")
        self.assertEqual(rows[-1]["coin"], "TRU-USD")

    def test_single_coin_paths_are_isolated(self) -> None:
        rows = {row["coin"]: row for row in manifest.build_rows()}
        nom = rows["NOM-USD"]

        self.assertIn("multi_coin_portfolio_nomusd_state.json", nom["state_path"])
        self.assertIn("multi_coin_portfolio_nomusd_events.jsonl", nom["event_path"])
        self.assertIn("--coins NOM-USD", nom["launch_command"])
        self.assertIn("--max-loops 1", nom["smoke_command"])
        self.assertIn("--max-loops 12", nom["supervised_command"])


if __name__ == "__main__":
    unittest.main()
