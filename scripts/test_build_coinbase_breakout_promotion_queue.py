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

import build_coinbase_breakout_promotion_queue as board


class CoinbaseBreakoutPromotionQueueTests(unittest.TestCase):
    def test_build_payload_classifies_breakout_promotions_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_range_breakout_sweep.json").write_text(
                json.dumps(
                    {
                        "coin_rows": [
                            {
                                "coin": "NOM-USD",
                                "best_net_pnl": 2639.0,
                                "best_trades": 231,
                                "best_win_rate": 37.7,
                                "best_max_drawdown": 27.2,
                                "profitable_rate": 100.0,
                                "uplift_vs_default_momentum": 2152.96,
                                "best_range_lookback": 10,
                                "best_tp_pct": 10.0,
                                "best_sl_pct": 1.0,
                                "best_max_hold": 24,
                            },
                            {
                                "coin": "SUP-USD",
                                "best_net_pnl": 188.39,
                                "best_trades": 89,
                                "best_win_rate": 44.9,
                                "best_max_drawdown": 12.3,
                                "profitable_rate": 99.0,
                                "uplift_vs_default_momentum": 157.34,
                                "best_range_lookback": 8,
                                "best_tp_pct": 8.0,
                                "best_sl_pct": 1.0,
                                "best_max_hold": 24,
                            },
                            {
                                "coin": "PRL-USD",
                                "best_net_pnl": 67.45,
                                "best_trades": 104,
                                "best_win_rate": 33.7,
                                "best_max_drawdown": 23.4,
                                "profitable_rate": 77.2,
                                "uplift_vs_default_momentum": 67.26,
                                "best_range_lookback": 25,
                                "best_tp_pct": 10.0,
                                "best_sl_pct": 1.0,
                                "best_max_hold": 36,
                            },
                            {
                                "coin": "BAL-USD",
                                "best_net_pnl": 47.16,
                                "best_trades": 30,
                                "best_win_rate": 56.7,
                                "best_max_drawdown": 10.7,
                                "profitable_rate": 80.0,
                                "uplift_vs_default_momentum": 35.06,
                                "best_range_lookback": 50,
                                "best_tp_pct": 10.0,
                                "best_sl_pct": 3.0,
                                "best_max_hold": 36,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_router_conflict_board.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "coin": "PRL-USD",
                                "conflict_action": "keep_rsi_primary_momentum_shadow_candidate",
                                "rationale": "keep RSI primary",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            old_reports = board.REPORTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_sweep = board.SWEEP_PATH
            old_router = board.ROUTER_CONFLICT_PATH
            try:
                board.REPORTS = reports
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.SWEEP_PATH = reports / "coinbase_range_breakout_sweep.json"
                board.ROUTER_CONFLICT_PATH = reports / "coinbase_spot_router_conflict_board.json"
                payload = board.build_payload(now=datetime(2026, 4, 12, 18, 25, 0, tzinfo=timezone.utc))
            finally:
                board.REPORTS = old_reports
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.SWEEP_PATH = old_sweep
                board.ROUTER_CONFLICT_PATH = old_router

        by_key = {(row["coin"], row["strategy"]): row for row in payload["queue"] + payload["blocked_or_deferred"]}
        self.assertEqual(by_key[("NOM-USD", "range_breakout_shadow")]["action"], "launch_shadow_after_top_batch")
        self.assertEqual(by_key[("SUP-USD", "range_breakout_shadow")]["action"], "launch_shadow_after_top_batch")
        self.assertEqual(by_key[("BAL-USD", "range_breakout_shadow")]["action"], "launch_shadow_after_top_batch")
        self.assertEqual(by_key[("PRL-USD", "range_breakout_shadow")]["action"], "resolve_router_conflict")


if __name__ == "__main__":
    unittest.main()
