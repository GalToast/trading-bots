#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import optimize_allocation as allocation_mod
import optimal_portfolio_optimizer as portfolio_mod


class OptimizerSemanticsMetadataTests(unittest.TestCase):
    def test_allocation_optimizer_advertises_native_gated_semantics(self) -> None:
        semantics = allocation_mod.report_semantics_payload()
        assumptions = allocation_mod.native_assumptions_payload()
        artifacts = allocation_mod.comparison_artifacts_payload()
        canonical = allocation_mod.canonical_reference_payload([])

        self.assertEqual(semantics["surface_kind"], "native_gated_simulator")
        self.assertFalse(semantics["comparable_to_canonical_without_reconciliation"])
        self.assertTrue(assumptions["session_gate"])
        self.assertEqual(assumptions["deploy_fraction"], 0.90)
        self.assertIn("allocation_optimizer_reconciliation.json", artifacts["canonical_reconciliation_report"])
        self.assertTrue(canonical["available"])
        self.assertEqual(canonical["status"], "reconciled_divergent")
        self.assertEqual(canonical["source_mode"], "native_inline_replay")
        self.assertEqual(canonical["assumptions"]["min_cash"], 10.0)

    def test_optimal_portfolio_optimizer_advertises_native_gated_semantics(self) -> None:
        semantics = portfolio_mod.report_semantics_payload()
        assumptions = portfolio_mod.native_assumptions_payload()
        artifacts = portfolio_mod.comparison_artifacts_payload()
        canonical = portfolio_mod.canonical_reference_payload([], {})

        self.assertEqual(semantics["surface_kind"], "native_gated_simulator")
        self.assertFalse(semantics["comparable_to_canonical_without_reconciliation"])
        self.assertTrue(assumptions["session_gate"])
        self.assertEqual(assumptions["deploy_fraction"], 0.90)
        self.assertIn("optimal_portfolio_optimizer_reconciliation.json", artifacts["canonical_reconciliation_report"])
        self.assertIn("optimal_portfolio_drift_attribution.json", artifacts["drift_attribution_report"])
        self.assertTrue(canonical["available"])
        self.assertEqual(canonical["status"], "reconciled_divergent")
        self.assertEqual(canonical["source_mode"], "native_inline_replay")
        self.assertEqual(canonical["assumptions"]["min_cash"], 10.0)
        self.assertIn("drift_attribution", canonical)


if __name__ == "__main__":
    unittest.main()
