#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_readiness_audit as audit


class CoinbaseIsolatedRunnerReadinessAuditTests(unittest.TestCase):
    def test_audit_emits_expected_verdict(self) -> None:
        payload = audit.build_payload()

        self.assertEqual(payload["readiness_verdict"], "block_deploy_until_recovery_and_ops_gaps_close")
        self.assertEqual(len(payload["findings"]), 4)

    def test_audit_contains_recovery_findings(self) -> None:
        findings = {finding["title"]: finding for finding in audit.build_findings()}

        self.assertIn("Crash recovery restores cash only, not live positions", findings)
        self.assertIn("Backfill path cannot reconstruct an open position after restart", findings)
        self.assertGreater(findings["Crash recovery restores cash only, not live positions"]["line"], 0)


if __name__ == "__main__":
    unittest.main()
