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

import build_coinbase_spot_next_launch_wave as board


class CoinbaseSpotNextLaunchWaveTests(unittest.TestCase):
    def test_build_payload_orders_launches_and_holds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_momentum_promotion_queue.json").write_text(
                json.dumps(
                    {
                        "queue": [
                            {
                                "coin": "RAVE-USD",
                                "strategy": "mom_10",
                                "action": "keep_live_priority",
                                "score": 767.74,
                                "reconciliation_30d_net_usd": 763.89,
                                "reconciliation_30d_closes": 102,
                                "runtime_realized_usd": 10.2032,
                            },
                            {
                                "coin": "A8-USD",
                                "strategy": "mom_50",
                                "action": "launch_shadow_next",
                                "score": 37.28,
                                "reconciliation_30d_net_usd": 29.12,
                                "reconciliation_30d_closes": 48,
                                "runtime_realized_usd": None,
                            },
                            {
                                "coin": "CFG-USD",
                                "strategy": "mom_25",
                                "action": "launch_shadow_next",
                                "score": 37.0,
                                "reconciliation_30d_net_usd": 25.11,
                                "reconciliation_30d_closes": 91,
                                "runtime_realized_usd": None,
                            },
                        ],
                        "blocked_or_deferred": [
                            {
                                "coin": "PRL-USD",
                                "strategy": "mom_50",
                                "action": "resolve_router_conflict",
                                "score": 14.7,
                                "reconciliation_30d_net_usd": 17.74,
                                "reconciliation_30d_closes": 54,
                            },
                            {
                                "coin": "BLUR-USD",
                                "strategy": "mom_25",
                                "action": "debug_before_promotion",
                                "score": 22.7,
                                "reconciliation_30d_net_usd": 21.41,
                                "reconciliation_30d_closes": 34,
                                "runtime_realized_usd": -5.3696,
                            },
                            {
                                "coin": "DASH-USD",
                                "strategy": "mom_50",
                                "action": "watch_probe_only",
                                "score": 0.13,
                                "reconciliation_30d_net_usd": 0.4,
                                "reconciliation_30d_closes": 66,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_router_conflict_board.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {"coin": "RAVE-USD", "conflict_action": "anchor_momentum_keep_rsi_secondary"},
                            {"coin": "A8-USD", "conflict_action": "replace_negative_rsi_with_momentum_shadow"},
                            {"coin": "PRL-USD", "conflict_action": "keep_rsi_primary_momentum_shadow_candidate", "rationale": "keep RSI primary"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_runtime_board.json").write_text(
                json.dumps(
                    {
                        "key_lanes": [
                            {"product_id": "DOGE-USD", "realized_net_usd": 0.0, "action": "verify_probe_health"},
                            {"product_id": "XRP-USD", "realized_net_usd": 0.0, "action": "verify_probe_health"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_deployability_board.json").write_text(
                json.dumps(
                    {
                        "router": [
                            {"product_id": "RAVE-USD", "action": "promote_small_live"},
                            {"product_id": "PRL-USD", "action": "promote_small_live"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_momentum_validation_results.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "coin": "SUP-USD",
                                "verdict": "confirmed_positive",
                                "reconciliation_30d_net_usd": 48.06,
                                "reconciliation_30d_closes": 60,
                                "reconciliation_30d_max_dd": 23.1,
                            },
                            {
                                "coin": "TROLL-USD",
                                "verdict": "rejected",
                                "reconciliation_30d_net_usd": -3.17,
                                "reconciliation_30d_closes": 30,
                                "reconciliation_30d_max_dd": 50.6,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_breakout_promotion_queue.json").write_text(
                json.dumps(
                    {
                        "queue": [
                            {
                                "coin": "NOM-USD",
                                "strategy": "range_breakout_shadow",
                                "action": "launch_shadow_after_top_batch",
                                "score": 2859.3,
                                "reconciliation_30d_net_usd": 2639.0,
                                "reconciliation_30d_closes": 231,
                                "note": "breakout shadow after current momentum batch",
                            },
                            {
                                "coin": "SUP-USD",
                                "strategy": "range_breakout_shadow",
                                "action": "launch_shadow_after_top_batch",
                                "score": 209.0,
                                "reconciliation_30d_net_usd": 188.39,
                                "reconciliation_30d_closes": 89,
                                "note": "breakout shadow after current momentum batch",
                            },
                        ],
                        "blocked_or_deferred": [
                            {
                                "coin": "PRL-USD",
                                "strategy": "range_breakout_shadow",
                                "action": "resolve_router_conflict",
                                "score": 76.0,
                                "reconciliation_30d_net_usd": 67.45,
                                "reconciliation_30d_closes": 104,
                                "router_conflict_action": "keep_rsi_primary_momentum_shadow_candidate",
                                "note": "keep RSI primary",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            old_reports = board.REPORTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_momentum = board.MOMENTUM_PROMOTION_PATH
            old_conflict = board.ROUTER_CONFLICT_PATH
            old_runtime = board.RUNTIME_BOARD_PATH
            old_deploy = board.DEPLOYABILITY_BOARD_PATH
            old_validation = board.VALIDATION_RESULTS_PATH
            old_breakout = board.BREAKOUT_PROMOTION_PATH
            try:
                board.REPORTS = reports
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.MOMENTUM_PROMOTION_PATH = reports / "coinbase_momentum_promotion_queue.json"
                board.ROUTER_CONFLICT_PATH = reports / "coinbase_spot_router_conflict_board.json"
                board.RUNTIME_BOARD_PATH = reports / "coinbase_spot_runtime_board.json"
                board.DEPLOYABILITY_BOARD_PATH = reports / "coinbase_spot_deployability_board.json"
                board.VALIDATION_RESULTS_PATH = reports / "coinbase_momentum_validation_results.json"
                board.BREAKOUT_PROMOTION_PATH = reports / "coinbase_breakout_promotion_queue.json"
                payload = board.build_payload(now=datetime(2026, 4, 12, 17, 50, 0, tzinfo=timezone.utc))
            finally:
                board.REPORTS = old_reports
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.MOMENTUM_PROMOTION_PATH = old_momentum
                board.ROUTER_CONFLICT_PATH = old_conflict
                board.RUNTIME_BOARD_PATH = old_runtime
                board.DEPLOYABILITY_BOARD_PATH = old_deploy
                board.VALIDATION_RESULTS_PATH = old_validation
                board.BREAKOUT_PROMOTION_PATH = old_breakout

        by_key = {(row["coin"], row["strategy"]): row for row in payload["rows"]}
        self.assertEqual(by_key[("RAVE-USD", "mom_10")]["launch_wave"], "maintain_live")
        self.assertEqual(by_key[("A8-USD", "mom_50")]["launch_wave"], "launch_now")
        self.assertEqual(by_key[("CFG-USD", "mom_25")]["launch_wave"], "launch_now")
        self.assertEqual(by_key[("PRL-USD", "mom_50")]["launch_wave"], "router_hold")
        self.assertEqual(by_key[("PRL-USD", "range_breakout_shadow")]["launch_wave"], "router_hold")
        self.assertEqual(by_key[("BLUR-USD", "mom_25")]["launch_wave"], "debug_hold")
        self.assertEqual(by_key[("DASH-USD", "mom_50")]["launch_wave"], "watch_only")
        self.assertEqual(by_key[("DOGE-USD", "spot_piranha")]["launch_wave"], "router_hold")
        self.assertEqual(by_key[("SUP-USD", "momentum_registry_validation")]["launch_wave"], "launch_after_wave_1")
        self.assertEqual(by_key[("SUP-USD", "range_breakout_shadow")]["launch_wave"], "launch_after_wave_1")
        self.assertEqual(by_key[("NOM-USD", "range_breakout_shadow")]["launch_wave"], "launch_after_wave_1")
        self.assertEqual(by_key[("TROLL-USD", "momentum_registry_validation")]["launch_wave"], "debug_hold")


if __name__ == "__main__":
    unittest.main()
