#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_sleeve_smoke_manifest as manifest


class CoinbaseIsolatedRunnerSleeveSmokeManifestTests(unittest.TestCase):
    def test_manifest_uses_override_config_and_marks_exact_vs_inferred(self) -> None:
        payload = manifest.build_payload()
        rows = {row["coin"]: row for row in payload["rows"]}

        self.assertIn("--config-path", rows["TRU-USD"]["smoke_command"])
        self.assertEqual(rows["TRU-USD"]["proof_class"], "exact_config_smoke")
        self.assertEqual(rows["NOM-USD"]["proof_class"], "exact_config_smoke")
        self.assertEqual(rows["RAVE-USD"]["proof_class"], "inferred_config_smoke")
        self.assertEqual(rows["A8-USD"]["proof_class"], "inferred_config_smoke")


if __name__ == "__main__":
    unittest.main()
