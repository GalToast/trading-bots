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

import build_coinbase_spot_router_conflict_board as board


class CoinbaseSpotRouterConflictBoardTests(unittest.TestCase):
    def test_build_payload_assigns_router_actions(self) -> None:
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
                            },
                            {
                                "coin": "PRL-USD",
                                "strategy": "mom_50",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 17.74,
                                "reconciliation_closes_30d": 54,
                            },
                            {
                                "coin": "FARTCOIN-USD",
                                "strategy": "mom_50",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 0.71,
                                "reconciliation_closes_30d": 77,
                            },
                            {
                                "coin": "A8-USD",
                                "strategy": "mom_50",
                                "family": "momentum",
                                "verdict": "bench_positive_wait_runtime",
                                "reconciliation_net_30d_usd": 29.12,
                                "reconciliation_closes_30d": 48,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_runtime_board.json").write_text(
                json.dumps(
                    {
                        "key_lanes": [
                            {
                                "product_id": "RAVE-USD",
                                "lane": "rave_rsi_mr_live_v2",
                                "family": "rsi_mean_reversion",
                                "status": "active",
                                "action": "monitor_open_position",
                                "realized_net_usd": 131.3795,
                                "closes": 17,
                            }
                        ],
                        "rsi_shadow_queue": [
                            {
                                "product_id": "PRL-USD",
                                "lane": "shadow_coinbase_prlusd_rsi7",
                                "status": "active",
                                "action": "promote_small_live",
                                "realized_net_usd": 0.1371,
                                "closes": 14,
                            },
                            {
                                "product_id": "FARTCOIN-USD",
                                "lane": "shadow_coinbase_fartcoinusd_rsi7",
                                "status": "active",
                                "action": "promote_small_live",
                                "realized_net_usd": 0.7983,
                                "closes": 14,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_deployability_board.json").write_text(
                json.dumps(
                    {
                        "rejects": [
                            {
                                "product_id": "A8-USD",
                                "lane": "shadow_coinbase_a8usd_rsi4",
                                "family": "rsi_mean_reversion",
                                "runner_status": "active",
                                "action": "reject",
                                "observed_net_usd": -0.1817,
                                "note": "shadow realized=$-0.1817",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_momentum_promotion_queue.json").write_text(
                json.dumps(
                    {
                        "queue": [
                            {"coin": "RAVE-USD", "action": "keep_live_priority"},
                            {"coin": "A8-USD", "action": "launch_shadow_next"},
                        ],
                        "blocked_or_deferred": [
                            {"coin": "PRL-USD", "action": "resolve_router_conflict"},
                            {"coin": "FARTCOIN-USD", "action": "resolve_router_conflict"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            old_reports = board.REPORTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_evidence = board.EVIDENCE_MATRIX_PATH
            old_runtime = board.RUNTIME_BOARD_PATH
            old_deploy = board.DEPLOYABILITY_BOARD_PATH
            old_promotion = board.MOMENTUM_PROMOTION_PATH
            try:
                board.REPORTS = reports
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.EVIDENCE_MATRIX_PATH = reports / "coinbase_spot_evidence_matrix.json"
                board.RUNTIME_BOARD_PATH = reports / "coinbase_spot_runtime_board.json"
                board.DEPLOYABILITY_BOARD_PATH = reports / "coinbase_spot_deployability_board.json"
                board.MOMENTUM_PROMOTION_PATH = reports / "coinbase_momentum_promotion_queue.json"
                payload = board.build_payload(now=datetime(2026, 4, 12, 17, 45, 0, tzinfo=timezone.utc))
            finally:
                board.REPORTS = old_reports
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.EVIDENCE_MATRIX_PATH = old_evidence
                board.RUNTIME_BOARD_PATH = old_runtime
                board.DEPLOYABILITY_BOARD_PATH = old_deploy
                board.MOMENTUM_PROMOTION_PATH = old_promotion

        by_coin = {row["coin"]: row for row in payload["rows"]}
        self.assertEqual(by_coin["RAVE-USD"]["conflict_action"], "anchor_momentum_keep_rsi_secondary")
        self.assertEqual(by_coin["PRL-USD"]["conflict_action"], "keep_rsi_primary_momentum_shadow_candidate")
        self.assertEqual(by_coin["FARTCOIN-USD"]["conflict_action"], "keep_rsi_only_for_now")
        self.assertEqual(by_coin["A8-USD"]["conflict_action"], "replace_negative_rsi_with_momentum_shadow")


if __name__ == "__main__":
    unittest.main()
