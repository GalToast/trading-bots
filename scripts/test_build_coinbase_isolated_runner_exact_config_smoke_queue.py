#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_exact_config_smoke_queue as queue_builder


class CoinbaseIsolatedRunnerExactConfigSmokeQueueTests(unittest.TestCase):
    def test_queue_prioritizes_exact_config_and_tru_first(self) -> None:
        payload = queue_builder.build_payload()
        rows = payload["rows"]

        self.assertEqual(payload["summary"]["verification_verdict"], "restart_drill_verified_for_controlled_smoke")
        self.assertEqual(payload["summary"]["first_smoke_candidate"], "TRU-USD momentum_registry_validation")
        self.assertEqual([row["coin"] for row in rows[:4]], ["TRU-USD", "NOM-USD", "SUP-USD", "BAL-USD"])
        self.assertTrue(all(row["proof_class"] == "exact_config_smoke" for row in rows[:4]))

    def test_queue_demotes_inferred_rows_and_rave_is_optional_last(self) -> None:
        payload = queue_builder.build_payload()
        rows = {row["coin"]: row for row in payload["rows"]}

        self.assertEqual(rows["A8-USD"]["phase"], "batch_2_inferred_launch_now")
        self.assertEqual(rows["CFG-USD"]["phase"], "batch_2_inferred_launch_now")
        self.assertEqual(rows["RAVE-USD"]["phase"], "batch_3_inferred_optional")
        self.assertEqual(rows["BAL-USD"]["queue_decision"], "run_after_exact_batch_once_legacy_runtime_is_retired")


if __name__ == "__main__":
    unittest.main()
