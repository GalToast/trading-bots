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

import build_coinbase_spot_evidence_matrix as matrix


class CoinbaseSpotEvidenceMatrixTests(unittest.TestCase):
    def test_build_payload_classifies_key_combos(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "backtest_reconciliation.json").write_text(
                json.dumps(
                    {
                        "individual_full_cash": {
                            "Momentum (RAVE)": {"net_pnl": 763.89, "closes": 102},
                            "RSI MR (RAVE)": {"net_pnl": 204.72, "closes": 75},
                            "BB Reversion (IOTX)": {"net_pnl": -35.46, "closes": 144},
                            "Momentum (BAL)": {"net_pnl": 36.59, "closes": 32},
                            "Momentum (BLUR)": {"net_pnl": 21.41, "closes": 34},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_opportunity_sweep_partial.json").write_text(
                json.dumps(
                    {
                        "run_at": "2026-04-12T17:18:02.133875+00:00",
                        "profitable_combos": [
                            {"coin": "RAVE-USD", "strategy": "mom_10", "net_pnl": 371.69, "closes": 60},
                            {"coin": "IOTX-USD", "strategy": "mom_25", "net_pnl": 13.65, "closes": 19},
                            {"coin": "BAL-USD", "strategy": "mom_50", "net_pnl": 36.88, "closes": 17},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (reports / "multi_coin_portfolio_state.json").write_text(
                json.dumps(
                    {
                        "coins": {
                            "RAVE-USD": {"strategy": "momentum", "realized_net": 10.2032, "closes": 6},
                            "IOTX-USD": {"strategy": "bb_reversion", "realized_net": -11.8308, "closes": 6},
                            "BAL-USD": {"strategy": "momentum", "realized_net": 0.0, "closes": 0},
                            "BLUR-USD": {"strategy": "momentum", "realized_net": -5.3696, "closes": 1},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_deployability_board.json").write_text(
                json.dumps(
                    {
                        "candidates": [
                            {"product_id": "RAVE-USD", "family": "rsi_mean_reversion", "action": "restore_live"},
                            {"product_id": "IOTX-USD", "family": "bb_reversion", "action": "reconcile_first"},
                            {"product_id": "BAL-USD", "family": "momentum", "action": "reconcile_first"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "rave_rsi_mr_live_v2_state.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-04-12T17:29:50+00:00",
                        "state": {"realized_net": 131.3795, "closes": 17},
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_momentum_reconciliation_results.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-12T17:29:56.327288+00:00",
                        "results": [
                            {
                                "coin": "CFG-USD",
                                "strategy": "mom_25",
                                "verdict": "confirmed_positive",
                                "library_sweep_partial_14d_net_usd": 9.16,
                                "library_sweep_partial_14d_closes": 12,
                                "reconciliation_30d_net_usd": 25.11,
                                "reconciliation_30d_closes": 91,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            old_reports = matrix.REPORTS
            old_md = matrix.MD_PATH
            old_json = matrix.JSON_PATH
            old_recon = matrix.RECON_PATH
            old_sweep = matrix.SWEEP_PARTIAL_PATH
            old_runtime = matrix.RUNTIME_PATH
            old_deploy = matrix.DEPLOYABILITY_PATH
            old_rave_live = matrix.RAVE_LIVE_STATE_PATH
            old_momentum_recon = matrix.MOMENTUM_RECON_RESULTS_PATH
            try:
                matrix.REPORTS = reports
                matrix.MD_PATH = reports / "out.md"
                matrix.JSON_PATH = reports / "out.json"
                matrix.RECON_PATH = reports / "backtest_reconciliation.json"
                matrix.SWEEP_PARTIAL_PATH = reports / "coinbase_opportunity_sweep_partial.json"
                matrix.RUNTIME_PATH = reports / "multi_coin_portfolio_state.json"
                matrix.DEPLOYABILITY_PATH = reports / "coinbase_spot_deployability_board.json"
                matrix.RAVE_LIVE_STATE_PATH = reports / "rave_rsi_mr_live_v2_state.json"
                matrix.MOMENTUM_RECON_RESULTS_PATH = reports / "coinbase_momentum_reconciliation_results.json"
                payload = matrix.build_payload()
            finally:
                matrix.REPORTS = old_reports
                matrix.MD_PATH = old_md
                matrix.JSON_PATH = old_json
                matrix.RECON_PATH = old_recon
                matrix.SWEEP_PARTIAL_PATH = old_sweep
                matrix.RUNTIME_PATH = old_runtime
                matrix.DEPLOYABILITY_PATH = old_deploy
                matrix.RAVE_LIVE_STATE_PATH = old_rave_live
                matrix.MOMENTUM_RECON_RESULTS_PATH = old_momentum_recon

        by_id = {row["combo_id"]: row for row in payload["rows"]}
        self.assertEqual(by_id["rave_mom_10"]["verdict"], "deployable_priority")
        self.assertEqual(by_id["rave_rsi_mr"]["verdict"], "deployable_priority")
        self.assertEqual(by_id["iotx_bb_rev"]["verdict"], "reject_or_debug")
        self.assertEqual(by_id["bal_mom_50"]["verdict"], "bench_positive_wait_runtime")
        self.assertEqual(by_id["iotx_mom_25"]["verdict"], "explore_only")
        self.assertEqual(by_id["cfg_mom_25"]["verdict"], "bench_positive_wait_runtime")


if __name__ == "__main__":
    unittest.main()
