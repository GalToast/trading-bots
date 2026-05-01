#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_momentum_validation_inbox as inbox


class CoinbaseMomentumValidationInboxTests(unittest.TestCase):
    def test_build_payload_triages_registry_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "master_deployment_registry.md").write_text(
                "\n".join(
                    [
                        "# Master Deployment Registry",
                        "",
                        "## A-TIER: 7d Validated, 97%+ Param Hit Rate",
                        "",
                        "### RAVE-USD — Momentum",
                        "| Param | Value |",
                        "| **7d Net PnL** | **+$451.51** |",
                        "",
                        "### TRU-USD — Momentum",
                        "| Param | Value |",
                        "| **7d Net PnL** | **+$214.83** |",
                        "| **Param Hit Rate: 98.6%** (2,129/2,160 combos profitable) |",
                        "",
                        "### GHST-USD — Momentum",
                        "| Param | Value |",
                        "| **7d Net PnL** | **+$156.01** |",
                        "| **Param Hit Rate: 100.0%** (2,160/2,160 combos profitable) |",
                        "",
                        "## B-TIER: 7d Positive, Not Fully Swept",
                        "",
                        "| Coin | Strategy | 7d PnL | WR | Trades | Notes |",
                        "|------|----------|--------|----|--------|-------|",
                        "| TROLL-USD | Momentum (lb=30, tp=15, sl=5) | +$39.53 | 80.0% | 10 | High WR, low trades |",
                        "| SUP-USD | Momentum (lb=15, tp=5, sl=0) | +$32.43 | 76.2% | 42 | Good edge |",
                    ]
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_evidence_matrix.json").write_text(
                json.dumps({"rows": [{"coin": "RAVE-USD"}]}),
                encoding="utf-8",
            )
            (reports / "coinbase_momentum_reconciliation_results.json").write_text(
                json.dumps({"results": []}),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_next_launch_wave.json").write_text(
                json.dumps({"rows": []}),
                encoding="utf-8",
            )

            old_reports = inbox.REPORTS
            old_md = inbox.MD_PATH
            old_json = inbox.JSON_PATH
            old_registry = inbox.REGISTRY_PATH
            old_evidence = inbox.EVIDENCE_MATRIX_PATH
            old_recon = inbox.MOMENTUM_RECON_RESULTS_PATH
            old_launch = inbox.NEXT_LAUNCH_WAVE_PATH
            try:
                inbox.REPORTS = reports
                inbox.MD_PATH = reports / "out.md"
                inbox.JSON_PATH = reports / "out.json"
                inbox.REGISTRY_PATH = reports / "master_deployment_registry.md"
                inbox.EVIDENCE_MATRIX_PATH = reports / "coinbase_spot_evidence_matrix.json"
                inbox.MOMENTUM_RECON_RESULTS_PATH = reports / "coinbase_momentum_reconciliation_results.json"
                inbox.NEXT_LAUNCH_WAVE_PATH = reports / "coinbase_spot_next_launch_wave.json"
                payload = inbox.build_payload(now=datetime(2026, 4, 12, 17, 55, 0, tzinfo=timezone.utc))
            finally:
                inbox.REPORTS = old_reports
                inbox.MD_PATH = old_md
                inbox.JSON_PATH = old_json
                inbox.REGISTRY_PATH = old_registry
                inbox.EVIDENCE_MATRIX_PATH = old_evidence
                inbox.MOMENTUM_RECON_RESULTS_PATH = old_recon
                inbox.NEXT_LAUNCH_WAVE_PATH = old_launch

        inbox_by_coin = {row["coin"]: row for row in payload["validation_inbox"]}
        covered_by_coin = {row["coin"]: row for row in payload["already_covered"]}
        self.assertEqual(inbox_by_coin["TRU-USD"]["action"], "validate_30d_next")
        self.assertEqual(inbox_by_coin["GHST-USD"]["action"], "validate_30d_next")
        self.assertEqual(inbox_by_coin["TROLL-USD"]["action"], "optimize_then_validate")
        self.assertEqual(inbox_by_coin["SUP-USD"]["action"], "optimize_then_validate")
        self.assertEqual(covered_by_coin["RAVE-USD"]["note"], "already represented in the current board stack")


if __name__ == "__main__":
    unittest.main()
