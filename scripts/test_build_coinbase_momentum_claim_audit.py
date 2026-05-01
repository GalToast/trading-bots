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

import build_coinbase_momentum_claim_audit as audit


class CoinbaseMomentumClaimAuditTests(unittest.TestCase):
    def test_build_payload_uses_all_sweep_files_and_distinguishes_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_momentum_validation_results.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "coin": "SUP-USD",
                                "registry_strategy": "Momentum (lb=15, tp=5, sl=0)",
                                "reconciliation_30d_net_usd": 48.06,
                                "verdict": "confirmed_positive",
                            },
                            {
                                "coin": "MDT-USD",
                                "registry_strategy": "Momentum (lb=15, tp=5, sl=0)",
                                "reconciliation_30d_net_usd": 6.08,
                                "verdict": "confirmed_positive",
                            },
                            {
                                "coin": "TROLL-USD",
                                "registry_strategy": "Momentum (lb=30, tp=15, sl=5)",
                                "reconciliation_30d_net_usd": -3.17,
                                "verdict": "rejected",
                            },
                            {
                                "coin": "TRU-USD",
                                "registry_strategy": "Momentum",
                                "reconciliation_30d_net_usd": 395.17,
                                "verdict": "confirmed_positive",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "reconciliation_troll_sup_mdt.txt").write_text(
                "\n".join(
                    [
                        "TROLL-USD: 2085 candles",
                        "  BEST: lb=50 TP=10 SL=0: Net=$+60.24 WR=63.6% T=22 DD=19.8%",
                        "  Hit rate: 37.0% (10/27 combos profitable)",
                        "SUP-USD: 1856 candles",
                        "  BEST: lb=25 TP=15 SL=3: Net=$+137.31 WR=40.5% T=37 DD=19.3%",
                        "  Hit rate: 96.3% (26/27 combos profitable)",
                        "MDT-USD: 2004 candles",
                        "  BEST: lb=25 TP=5 SL=3: Net=$+69.65 WR=64.0% T=50 DD=14.7%",
                        "  Hit rate: 51.9% (14/27 combos profitable)",
                    ]
                ),
                encoding="utf-8",
            )
            (reports / "reconciliation_tru_ghst_red_nom.txt").write_text(
                "\n".join(
                    [
                        "TRU-USD: 2200 candles",
                        "  BEST: lb=10 TP=10 SL=3: Net=$+510.97 WR=51.3% T=78 DD=26.5%",
                        "  Hit rate: 85.2% (23/27 combos profitable)",
                    ]
                ),
                encoding="utf-8",
            )

            old_reports = audit.REPORTS
            old_md = audit.MD_PATH
            old_json = audit.JSON_PATH
            old_validation = audit.VALIDATION_RESULTS_PATH
            old_sweeps = audit.SWEEP_PATHS
            try:
                audit.REPORTS = reports
                audit.MD_PATH = reports / "out.md"
                audit.JSON_PATH = reports / "out.json"
                audit.VALIDATION_RESULTS_PATH = reports / "coinbase_momentum_validation_results.json"
                audit.SWEEP_PATHS = [
                    reports / "reconciliation_troll_sup_mdt.txt",
                    reports / "reconciliation_tru_ghst_red_nom.txt",
                ]
                payload = audit.build_payload(now=datetime(2026, 4, 12, 18, 0, 0, tzinfo=timezone.utc))
            finally:
                audit.REPORTS = old_reports
                audit.MD_PATH = old_md
                audit.JSON_PATH = old_json
                audit.VALIDATION_RESULTS_PATH = old_validation
                audit.SWEEP_PATHS = old_sweeps

        by_coin = {row["coin"]: row for row in payload["rows"]}
        self.assertEqual(by_coin["SUP-USD"]["audit_verdict"], "robust_confirmed")
        self.assertEqual(by_coin["MDT-USD"]["audit_verdict"], "robust_confirmed")
        self.assertEqual(by_coin["TROLL-USD"]["audit_verdict"], "optimize_only")
        self.assertEqual(by_coin["TRU-USD"]["audit_verdict"], "robust_confirmed")
        self.assertTrue(
            any("TRU" in line and "positive on the local optimized sweep" in line for line in payload["leadership_read"])
        )


if __name__ == "__main__":
    unittest.main()
