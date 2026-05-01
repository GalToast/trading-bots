#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_momentum_reconciliation_queue as queue_builder


class CoinbaseMomentumReconciliationQueueTests(unittest.TestCase):
    def test_build_payload_sorts_fresh_momentum_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_opportunity_sweep_partial.json").write_text(
                json.dumps(
                    {
                        "run_at": "2026-04-12T17:21:22.030503+00:00",
                        "profitable_combos": [
                            {"coin": "RAVE-USD", "strategy": "mom_10", "net_pnl": 371.69, "closes": 60, "win_rate": 73.3, "max_dd": 24.8},
                            {"coin": "IOTX-USD", "strategy": "mom_25", "net_pnl": 13.65, "closes": 19, "win_rate": 47.4, "max_dd": 16.1},
                            {"coin": "CFG-USD", "strategy": "mom_25", "net_pnl": 9.16, "closes": 42, "win_rate": 50.0, "max_dd": 13.4},
                            {"coin": "MOG-USD", "strategy": "mom_50", "net_pnl": 3.02, "closes": 1, "win_rate": 100.0, "max_dd": 0.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_evidence_matrix.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {"coin": "RAVE-USD", "strategy": "mom_10", "verdict": "deployable_priority"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            old_reports = queue_builder.REPORTS
            old_md = queue_builder.MD_PATH
            old_json = queue_builder.JSON_PATH
            old_sweep = queue_builder.SWEEP_PARTIAL_PATH
            old_matrix = queue_builder.EVIDENCE_MATRIX_PATH
            try:
                queue_builder.REPORTS = reports
                queue_builder.MD_PATH = reports / "out.md"
                queue_builder.JSON_PATH = reports / "out.json"
                queue_builder.SWEEP_PARTIAL_PATH = reports / "coinbase_opportunity_sweep_partial.json"
                queue_builder.EVIDENCE_MATRIX_PATH = reports / "coinbase_spot_evidence_matrix.json"
                payload = queue_builder.build_payload()
            finally:
                queue_builder.REPORTS = old_reports
                queue_builder.MD_PATH = old_md
                queue_builder.JSON_PATH = old_json
                queue_builder.SWEEP_PARTIAL_PATH = old_sweep
                queue_builder.EVIDENCE_MATRIX_PATH = old_matrix

        self.assertTrue(any(row["coin"] == "IOTX-USD" and row["priority"] == "reconcile_next" for row in payload["queue"]))
        self.assertTrue(any(row["coin"] == "CFG-USD" and row["priority"] == "reconcile_next" for row in payload["queue"]))
        self.assertTrue(any(row["coin"] == "MOG-USD" and row["priority"] == "watch_only" for row in payload["queue"]))
        self.assertTrue(any(row["coin"] == "RAVE-USD" for row in payload["already_covered"]))


if __name__ == "__main__":
    unittest.main()
