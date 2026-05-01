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

import build_coinbase_spot_deployability_board as board


class CoinbaseSpotDeployabilityBoardTests(unittest.TestCase):
    def test_build_payload_ranks_verified_and_contested_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            scripts = root / "scripts"
            reports.mkdir(parents=True)
            scripts.mkdir(parents=True)

            (reports / "coinbase_spot_rsi_scoreboard.csv").write_text(
                "lane_name,product_id,readiness_verdict,baseline_72h_net_usd,walkforward,realized_net_usd,realized_closes,in_position,cash_usd,total_fees,signals_generated,heartbeat_age_seconds,pid,state_path,note\n"
                "shadow_coinbase_vvvusd_rsi7,VVV-USD,probationary,1.15,3/3,1.07,27,0,48,1.0,10,26.7,1,foo,\n"
                "shadow_coinbase_a8usd_rsi4,A8-USD,unrated,0,-,-0.18,1,0,48,1.0,10,17.6,1,bar,\n",
                encoding="utf-8",
            )
            (reports / "rave_rsi_mr_live_v2_state.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-04-12T14:41:57+00:00",
                        "state": {
                            "realized_net": 235.7467,
                            "closes": 17,
                            "win_rate": 76.47,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (reports / "benchmark_engine_reconciliation.json").write_text(
                json.dumps({"models": {"realistic": {"harness": {"net_pnl": 222.34}}}}),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_piranha_candidates_72h.csv").write_text(
                "product_id,sim_realized_usd,sim_closes\n"
                "DOGE-USD,0.2046,3\n"
                "SUI-USD,0.2739,4\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_shadow_dogeusd_piranha_state.json").write_text(
                json.dumps(
                    {
                        "metadata": {"product_id": "DOGE-USD"},
                        "runner": {"heartbeat_at": "2026-04-12T16:47:36+00:00"},
                        "symbols": {"DOGE-USD": {"realized_net_usd": 0.0, "open_lots": [{}, {}]}},
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_shadow_solusd_piranha_state.json").write_text(
                json.dumps(
                    {
                        "metadata": {"product_id": "SOL-USD"},
                        "runner": {"heartbeat_at": "2026-04-11T15:30:53+00:00"},
                        "symbols": {"SOL-USD": {"realized_net_usd": 0.0, "open_lots": []}},
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_tactics_72h.csv").write_text(
                "tactic,fee_bps_per_side,best_product_id,realized_net_usd,ending_cash_usd,trades,median_hold_minutes,notes\n"
                "maker_scavenger,40,AVAX-USD,0.2815,48.28,5,793,test\n"
                "relative_strength_rotator,60,multi-asset,-16.68,31.31,36,50.5,test\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_flush_reclaim_72h.csv").write_text(
                "product_id,signals,cumulative_net_pct\nRAVE-USD,2,-5.0\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_pullback_resume_72h.csv").write_text(
                "product_id,signals,cumulative_net_pct\nRAVE-USD,2,-3.0\n",
                encoding="utf-8",
            )
            (reports / "multi_strategy_portfolio_results.json").write_text(
                json.dumps(
                    {
                        "equal_allocation": {
                            "individual": [
                                {"name": "IOTX BB Rev", "net_pnl": -4.79},
                                {"name": "BAL Momentum", "net_pnl": 7.32, "return_pct": 76.2, "win_rate": 46.9, "closes": 32},
                                {"name": "BLUR Momentum", "net_pnl": -8.91, "return_pct": -92.8, "win_rate": 48.5, "closes": 33},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            (scripts / "live_iotx_bb_reversion.py").write_text(
                '"""Backtest: 79.1% WR, $44/mo, 11.4% DD, RAR 3.86"""\n',
                encoding="utf-8",
            )

            old_reports = board.REPORTS
            old_scripts = board.SCRIPTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_rsi = board.RSI_SCOREBOARD_PATH
            old_rave_state = board.RAVE_LIVE_STATE_PATH
            old_recon = board.RAVE_RECON_PATH
            old_tactics = board.TACTICS_PATH
            old_piranha_candidates = board.PIRANHA_CANDIDATE_PATHS
            old_piranha_states = board.PIRANHA_STATE_PATHS
            old_reclaim = board.RECLAIM_PATH
            old_pullback = board.PULLBACK_PATH
            old_portfolio = board.PORTFOLIO_PATH
            old_iotx_script = board.IOTX_SCRIPT_PATH
            try:
                board.REPORTS = reports
                board.SCRIPTS = scripts
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.RSI_SCOREBOARD_PATH = reports / "coinbase_spot_rsi_scoreboard.csv"
                board.RAVE_LIVE_STATE_PATH = reports / "rave_rsi_mr_live_v2_state.json"
                board.RAVE_RECON_PATH = reports / "benchmark_engine_reconciliation.json"
                board.TACTICS_PATH = reports / "coinbase_spot_tactics_72h.csv"
                board.PIRANHA_CANDIDATE_PATHS = [reports / "coinbase_spot_piranha_candidates_72h.csv"]
                board.PIRANHA_STATE_PATHS = [
                    reports / "coinbase_spot_shadow_dogeusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_solusd_piranha_state.json",
                ]
                board.RECLAIM_PATH = reports / "coinbase_spot_flush_reclaim_72h.csv"
                board.PULLBACK_PATH = reports / "coinbase_spot_pullback_resume_72h.csv"
                board.PORTFOLIO_PATH = reports / "multi_strategy_portfolio_results.json"
                board.IOTX_SCRIPT_PATH = scripts / "live_iotx_bb_reversion.py"
                payload = board.build_payload(now=datetime(2026, 4, 12, 16, 48, 0, tzinfo=timezone.utc))
            finally:
                board.REPORTS = old_reports
                board.SCRIPTS = old_scripts
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.RSI_SCOREBOARD_PATH = old_rsi
                board.RAVE_LIVE_STATE_PATH = old_rave_state
                board.RAVE_RECON_PATH = old_recon
                board.TACTICS_PATH = old_tactics
                board.PIRANHA_CANDIDATE_PATHS = old_piranha_candidates
                board.PIRANHA_STATE_PATHS = old_piranha_states
                board.RECLAIM_PATH = old_reclaim
                board.PULLBACK_PATH = old_pullback
                board.PORTFOLIO_PATH = old_portfolio
                board.IOTX_SCRIPT_PATH = old_iotx_script

        self.assertEqual(payload["candidates"][0]["product_id"], "RAVE-USD")
        self.assertEqual(payload["candidates"][0]["action"], "restore_live")
        self.assertIn("Restore the RAVE live RSI lane", payload["leadership_read"][0])
        self.assertTrue(any(row["product_id"] == "VVV-USD" and row["action"] == "promote_small_live" for row in payload["candidates"]))
        self.assertTrue(any(row["product_id"] == "DOGE-USD" and row["action"] == "keep_shadow" for row in payload["candidates"]))
        self.assertTrue(any(row["product_id"] == "SUI-USD" and row["action"] == "launch_shadow" for row in payload["candidates"]))
        self.assertTrue(any(row["product_id"] == "IOTX-USD" and row["action"] == "reconcile_first" for row in payload["candidates"]))
        self.assertTrue(any(row["product_id"] == "BLUR-USD" and row["action"] == "reject" for row in payload["rejects"]))


if __name__ == "__main__":
    unittest.main()
