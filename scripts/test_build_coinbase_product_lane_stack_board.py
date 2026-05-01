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

import build_coinbase_product_lane_stack_board as board


class CoinbaseProductLaneStackBoardTests(unittest.TestCase):
    def test_build_payload_creates_same_coin_stack_policies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_spot_next_launch_wave.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {"coin": "RAVE-USD", "strategy": "mom_10", "launch_wave": "maintain_live", "reconciliation_30d_net_usd": 763.89, "reconciliation_30d_closes": 102, "router_decision": "anchor_momentum_keep_rsi_secondary"},
                            {"coin": "NOM-USD", "strategy": "range_breakout_shadow", "launch_wave": "launch_after_wave_1", "reconciliation_30d_net_usd": 2639.0, "reconciliation_30d_closes": 231, "router_decision": "breakout_shadow_candidate"},
                            {"coin": "NOM-USD", "strategy": "momentum_registry_validation", "launch_wave": "launch_after_wave_1", "reconciliation_30d_net_usd": 1260.08, "reconciliation_30d_closes": 122, "router_decision": "confirmed_positive"},
                            {"coin": "PRL-USD", "strategy": "range_breakout_shadow", "launch_wave": "router_hold", "reconciliation_30d_net_usd": 67.45, "reconciliation_30d_closes": 104, "router_decision": "keep_rsi_primary_momentum_shadow_candidate"},
                            {"coin": "PRL-USD", "strategy": "mom_50", "launch_wave": "router_hold", "reconciliation_30d_net_usd": 17.74, "reconciliation_30d_closes": 54, "router_decision": "keep_rsi_primary_momentum_shadow_candidate"},
                            {"coin": "A8-USD", "strategy": "mom_50", "launch_wave": "launch_now", "reconciliation_30d_net_usd": 29.12, "reconciliation_30d_closes": 48, "router_decision": "replace_negative_rsi_with_momentum_shadow"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_runtime_board.json").write_text(
                json.dumps(
                    {
                        "rsi_shadow_queue": [
                            {"product_id": "PRL-USD", "family": "rsi_mean_reversion", "status": "active", "lane": "shadow_coinbase_prlusd_rsi7", "action": "promote_small_live", "realized_net_usd": 0.1371, "closes": 14}
                        ],
                        "key_lanes": [
                            {"product_id": "RAVE-USD", "family": "rsi_mean_reversion", "status": "active", "lane": "rave_rsi_mr_live_v2", "action": "monitor_open_position", "realized_net_usd": 131.3795, "closes": 17}
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
                            {"coin": "PRL-USD", "conflict_action": "keep_rsi_primary_momentum_shadow_candidate"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_deployability_board.json").write_text(
                json.dumps(
                    {
                        "rejects": [
                            {"product_id": "A8-USD", "family": "rsi_mean_reversion", "runner_status": "active", "action": "reject", "observed_net_usd": -0.1817, "realized_closes": 0}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            old_next = board.NEXT_LAUNCH_WAVE_PATH
            old_runtime = board.RUNTIME_BOARD_PATH
            old_router = board.ROUTER_CONFLICT_PATH
            old_deploy = board.DEPLOYABILITY_BOARD_PATH
            old_json = board.JSON_PATH
            old_md = board.MD_PATH
            try:
                board.NEXT_LAUNCH_WAVE_PATH = reports / "coinbase_spot_next_launch_wave.json"
                board.RUNTIME_BOARD_PATH = reports / "coinbase_spot_runtime_board.json"
                board.ROUTER_CONFLICT_PATH = reports / "coinbase_spot_router_conflict_board.json"
                board.DEPLOYABILITY_BOARD_PATH = reports / "coinbase_spot_deployability_board.json"
                board.JSON_PATH = reports / "out.json"
                board.MD_PATH = reports / "out.md"
                payload = board.build_payload(now=datetime(2026, 4, 12, 18, 30, 0, tzinfo=timezone.utc))
            finally:
                board.NEXT_LAUNCH_WAVE_PATH = old_next
                board.RUNTIME_BOARD_PATH = old_runtime
                board.ROUTER_CONFLICT_PATH = old_router
                board.DEPLOYABILITY_BOARD_PATH = old_deploy
                board.JSON_PATH = old_json
                board.MD_PATH = old_md

        by_coin = {row["coin"]: row for row in payload["rows"]}
        self.assertEqual(by_coin["RAVE-USD"]["stack_policy"], "dual_live_allowed")
        self.assertEqual(by_coin["RAVE-USD"]["max_live_lanes"], 2)
        self.assertEqual(by_coin["NOM-USD"]["stack_policy"], "parallel_shadows_allowed")
        self.assertEqual(by_coin["PRL-USD"]["stack_policy"], "keep_rsi_primary_shadow_cap_1")
        self.assertEqual(by_coin["A8-USD"]["stack_policy"], "replace_negative_rsi_with_momentum")


if __name__ == "__main__":
    unittest.main()
