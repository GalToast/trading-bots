#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_optimal_portfolio_drift_attribution as drift_mod


class OptimalPortfolioDriftAttributionTests(unittest.TestCase):
    def test_variants_cover_expected_semantic_changes(self) -> None:
        payload = drift_mod.build_payload()
        rows = {row["variant_id"]: row for row in payload["variants"]}

        self.assertIn("optimizer_native", rows)
        self.assertIn("session_gate_off", rows)
        self.assertIn("deploy_95", rows)
        self.assertIn("min_cash_10", rows)
        self.assertIn("canonical", rows)

    def test_canonical_beats_native_on_saved_100_dollar_assignment(self) -> None:
        payload = drift_mod.build_payload()
        rows = {row["variant_id"]: row for row in payload["variants"]}

        self.assertGreater(rows["canonical"]["total_net_pnl"], rows["optimizer_native"]["total_net_pnl"])
        self.assertEqual(rows["canonical"]["feasible_count"], rows["canonical"]["coin_count"])
        self.assertGreater(payload["summary"]["component_effects"]["session_gate_off"], 0.0)
        self.assertEqual(payload["summary"]["component_effects"]["min_cash_10"], 0.0)


if __name__ == "__main__":
    unittest.main()
