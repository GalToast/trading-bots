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

import build_coinbase_spot_long_only_opportunity_board as board


class CoinbaseSpotLongOnlyOpportunityBoardTests(unittest.TestCase):
    def test_build_payload_uses_available_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_spot_rsi_scoreboard.csv").write_text(
                "lane_name,product_id,readiness_verdict,baseline_72h_net_usd,walkforward,realized_net_usd,realized_closes,in_position,cash_usd,total_fees,signals_generated,heartbeat_age_seconds,pid,state_path,note\n"
                "shadow_coinbase_raveusd_rsi7,RAVE-USD,probationary,0,0/0,10.5,20,0,48,1.0,10,1,1,foo,\n"
                "shadow_coinbase_prlusd_rsi7,PRL-USD,probationary,0,0/0,1.2,3,0,48,1.0,10,1,1,bar,\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_tactics_72h.csv").write_text(
                "tactic,fee_bps_per_side,best_product_id,realized_net_usd,ending_cash_usd,trades,median_hold_minutes,notes\n"
                "maker_scavenger,40,AVAX-USD,0.28,48.28,5,793,test\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_piranha_candidates.csv").write_text(
                "Product,Sim PnL,Closes,Median Hold (m),Buy Step,Target\n"
                "SUI-USD,0.27,4,1000,0.012,0.018\n",
                encoding="utf-8",
            )
            payload = {
                "metadata": {"product_id": "XRP-USD"},
                "runner": {"pid": 1, "heartbeat_at": "x"},
                "symbols": {"XRP-USD": {"cash_usd": 42.0, "inventory_units": 1.5, "realized_net_usd": 0.0, "realized_closes": 0, "open_lots": [{}]}},
            }
            (reports / "coinbase_spot_shadow_xrpusd_piranha_state.json").write_text(json.dumps(payload), encoding="utf-8")
            (reports / "coinbase_spot_shadow_dogeusd_piranha_state.json").write_text(json.dumps({"metadata": {"product_id": "DOGE-USD"}, "runner": {}, "symbols": {"DOGE-USD": {}}}), encoding="utf-8")
            (reports / "coinbase_spot_shadow_solusd_piranha_state.json").write_text(json.dumps({"metadata": {"product_id": "SOL-USD"}, "runner": {}, "symbols": {"SOL-USD": {}}}), encoding="utf-8")
            (reports / "coinbase_spot_flush_reclaim_72h.csv").write_text(
                "product_id,signals,cumulative_net_pct\nRAVE-USD,2,-5.0\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_pullback_resume_72h.csv").write_text(
                "product_id,signals,cumulative_net_pct\nRAVE-USD,2,-3.0\n",
                encoding="utf-8",
            )
            (reports / "coinbase_spot_reclaim_param_sweep.csv").write_text(
                "config,positive_products,cumulative_net_pct\ncfg,0,-10.0\n",
                encoding="utf-8",
            )

            old_reports = board.REPORTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_long = board.LONG_ONLY_RSI_PRODUCTS_PATH
            old_rsi_scoreboard = board.RSI_SCOREBOARD_PATH
            old_tactics = board.TACTICS_PATH
            old_piranha_paths = board.PIRANHA_PATHS
            old_piranha_cands = board.PIRANHA_CANDIDATES_PATHS
            old_reclaim = board.RECLAIM_PATH
            old_pullback = board.PULLBACK_PATH
            old_sweep = board.RECLAIM_SWEEP_PATH
            try:
                board.REPORTS = reports
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.LONG_ONLY_RSI_PRODUCTS_PATH = reports / "coinbase_spot_cross_asset_products.csv"
                board.RSI_SCOREBOARD_PATH = reports / "coinbase_spot_rsi_scoreboard.csv"
                board.TACTICS_PATH = reports / "coinbase_spot_tactics_72h.csv"
                board.PIRANHA_PATHS = [
                    reports / "coinbase_spot_shadow_xrpusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_dogeusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_solusd_piranha_state.json",
                ]
                board.PIRANHA_CANDIDATES_PATHS = [reports / "coinbase_spot_piranha_candidates.csv"]
                board.RECLAIM_PATH = reports / "coinbase_spot_flush_reclaim_72h.csv"
                board.PULLBACK_PATH = reports / "coinbase_spot_pullback_resume_72h.csv"
                board.RECLAIM_SWEEP_PATH = reports / "coinbase_spot_reclaim_param_sweep.csv"
                payload = board.build_payload()
            finally:
                board.REPORTS = old_reports
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.LONG_ONLY_RSI_PRODUCTS_PATH = old_long
                board.RSI_SCOREBOARD_PATH = old_rsi_scoreboard
                board.TACTICS_PATH = old_tactics
                board.PIRANHA_PATHS = old_piranha_paths
                board.PIRANHA_CANDIDATES_PATHS = old_piranha_cands
                board.RECLAIM_PATH = old_reclaim
                board.PULLBACK_PATH = old_pullback
                board.RECLAIM_SWEEP_PATH = old_sweep

        self.assertEqual(payload["rsi_products"][0]["product_id"], "RAVE-USD")
        self.assertEqual(payload["tactics"]["maker_scavenger"]["best_product_id"], "AVAX-USD")
        self.assertEqual(payload["piranha_candidates"][0]["product_id"], "SUI-USD")
        self.assertTrue(any(row["product_id"] == "XRP-USD" for row in payload["live_piranha"]))
        self.assertEqual(payload["reclaim_sweep"]["best_positive_products"], 0)


if __name__ == "__main__":
    unittest.main()
