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

import build_coinbase_momentum_promotion_queue as queue_builder


class CoinbaseMomentumPromotionQueueTests(unittest.TestCase):
    def test_build_payload_separates_clean_launches_from_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_spot_evidence_matrix.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "coin": "RAVE-USD",
                                "strategy": "mom_10",
                                "family": "momentum",
                                "verdict": "deployable_priority",
                                "reconciliation_net_30d_usd": 763.89,
                                "reconciliation_closes_30d": 102,
                                "library_sweep_partial_14d_net_usd": 371.69,
                                "runtime_realized_usd": 10.2032,
                                "runtime_closes": 6,
                            },
                            {
                                "coin": "CFG-USD",
                                "strategy": "mom_25",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 25.11,
                                "reconciliation_closes_30d": 91,
                                "library_sweep_partial_14d_net_usd": 9.16,
                                "runtime_realized_usd": None,
                                "runtime_closes": None,
                            },
                            {
                                "coin": "A8-USD",
                                "strategy": "mom_50",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 29.12,
                                "reconciliation_closes_30d": 48,
                                "library_sweep_partial_14d_net_usd": 9.6,
                                "runtime_realized_usd": None,
                                "runtime_closes": None,
                            },
                            {
                                "coin": "PRL-USD",
                                "strategy": "mom_50",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 17.74,
                                "reconciliation_closes_30d": 54,
                                "library_sweep_partial_14d_net_usd": 10.01,
                                "runtime_realized_usd": None,
                                "runtime_closes": None,
                            },
                            {
                                "coin": "DASH-USD",
                                "strategy": "mom_50",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 0.4,
                                "reconciliation_closes_30d": 66,
                                "library_sweep_partial_14d_net_usd": 7.25,
                                "runtime_realized_usd": None,
                                "runtime_closes": None,
                            },
                            {
                                "coin": "BLUR-USD",
                                "strategy": "mom_25",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 21.41,
                                "reconciliation_closes_30d": 34,
                                "library_sweep_partial_14d_net_usd": 20.83,
                                "runtime_realized_usd": -5.3696,
                                "runtime_closes": 1,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_runtime_board.json").write_text(
                json.dumps(
                    {
                        "rsi_shadow_queue": [
                            {
                                "product_id": "PRL-USD",
                                "status": "active",
                                "action": "promote_small_live",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_deployability_board.json").write_text(
                json.dumps(
                    {
                        "router": [
                            {
                                "product_id": "PRL-USD",
                                "recommended_lane": "shadow_coinbase_prlusd_rsi7",
                                "action": "promote_small_live",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            old_reports = queue_builder.REPORTS
            old_md = queue_builder.MD_PATH
            old_json = queue_builder.JSON_PATH
            old_evidence = queue_builder.EVIDENCE_MATRIX_PATH
            old_runtime = queue_builder.RUNTIME_BOARD_PATH
            old_deploy = queue_builder.DEPLOYABILITY_BOARD_PATH
            try:
                queue_builder.REPORTS = reports
                queue_builder.MD_PATH = reports / "out.md"
                queue_builder.JSON_PATH = reports / "out.json"
                queue_builder.EVIDENCE_MATRIX_PATH = reports / "coinbase_spot_evidence_matrix.json"
                queue_builder.RUNTIME_BOARD_PATH = reports / "coinbase_spot_runtime_board.json"
                queue_builder.DEPLOYABILITY_BOARD_PATH = reports / "coinbase_spot_deployability_board.json"
                payload = queue_builder.build_payload(now=datetime(2026, 4, 12, 17, 40, 0, tzinfo=timezone.utc))
            finally:
                queue_builder.REPORTS = old_reports
                queue_builder.MD_PATH = old_md
                queue_builder.JSON_PATH = old_json
                queue_builder.EVIDENCE_MATRIX_PATH = old_evidence
                queue_builder.RUNTIME_BOARD_PATH = old_runtime
                queue_builder.DEPLOYABILITY_BOARD_PATH = old_deploy

        by_coin = {row["coin"]: row for row in payload["queue"] + payload["blocked_or_deferred"]}
        self.assertEqual(by_coin["RAVE-USD"]["action"], "keep_live_priority")
        self.assertEqual(by_coin["CFG-USD"]["action"], "launch_shadow_next")
        self.assertEqual(by_coin["A8-USD"]["action"], "launch_shadow_next")
        self.assertEqual(by_coin["PRL-USD"]["action"], "resolve_router_conflict")
        self.assertEqual(by_coin["DASH-USD"]["action"], "watch_probe_only")
        self.assertEqual(by_coin["BLUR-USD"]["action"], "debug_before_promotion")


if __name__ == "__main__":
    unittest.main()
