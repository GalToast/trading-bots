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

import build_coinbase_spot_runtime_board as board


class CoinbaseSpotRuntimeBoardTests(unittest.TestCase):
    def test_runtime_board_prioritizes_stale_rave_and_active_probes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "rave_rsi_mr_live_v2_state.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-04-12T14:41:57+00:00",
                        "state": {
                            "realized_net": 235.7467,
                            "closes": 17,
                            "position": {"hold": 37, "tp": 3.222875},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (reports / "multi_coin_portfolio_state.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-04-12T16:53:33+00:00",
                        "portfolio_realized": -6.9972,
                        "portfolio_closes": 13,
                        "portfolio_wr": 46.2,
                        "total_starting_cash": 500.0,
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_rsi_scoreboard.csv").write_text(
                "lane_name,product_id,readiness_verdict,baseline_72h_net_usd,walkforward,realized_net_usd,realized_closes,in_position,cash_usd,total_fees,signals_generated,heartbeat_age_seconds,pid,state_path,note\n"
                "shadow_coinbase_vvvusd_rsi7,VVV-USD,probationary,1.15,3/3,1.07,27,0,48,1.0,10,26.7,1,foo,\n"
                "shadow_coinbase_prlusd_rsi7,PRL-USD,probationary,2.25,3/3,0.13,14,0,48,1.0,10,1200.0,1,bar,\n",
                encoding="utf-8",
            )
            for product_id, heartbeat_at in [
                ("DOGE-USD", "2026-04-12T16:53:34+00:00"),
                ("XRP-USD", "2026-04-12T16:53:35+00:00"),
                ("SOL-USD", "2026-04-11T15:30:53+00:00"),
            ]:
                lower = product_id.lower().replace("-", "").replace("usd", "usd")
                filename = {
                    "DOGE-USD": "coinbase_spot_shadow_dogeusd_piranha_state.json",
                    "XRP-USD": "coinbase_spot_shadow_xrpusd_piranha_state.json",
                    "SOL-USD": "coinbase_spot_shadow_solusd_piranha_state.json",
                }[product_id]
                realized = 0.0
                open_lots = [{}, {}] if product_id == "DOGE-USD" else ([{}, {}, {}] if product_id == "XRP-USD" else [])
                closes = 0
                cash_usd = 35.928 if product_id == "DOGE-USD" else (29.892 if product_id == "XRP-USD" else 48.0)
                (reports / filename).write_text(
                    json.dumps(
                        {
                            "metadata": {"product_id": product_id},
                            "runner": {"heartbeat_at": heartbeat_at},
                            "symbols": {product_id: {"realized_net_usd": realized, "realized_closes": closes, "open_lots": open_lots, "cash_usd": cash_usd}},
                        }
                    ),
                    encoding="utf-8",
                )

            old_reports = board.REPORTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_rave = board.RAVE_LIVE_STATE_PATH
            old_portfolio = board.MULTI_COIN_PORTFOLIO_STATE_PATH
            old_rsi = board.RSI_SCOREBOARD_PATH
            old_piranha = board.PIRANHA_STATE_PATHS
            old_iotx = board.STANDALONE_IOTX_PATH
            try:
                board.REPORTS = reports
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.RAVE_LIVE_STATE_PATH = reports / "rave_rsi_mr_live_v2_state.json"
                board.MULTI_COIN_PORTFOLIO_STATE_PATH = reports / "multi_coin_portfolio_state.json"
                board.RSI_SCOREBOARD_PATH = reports / "coinbase_spot_rsi_scoreboard.csv"
                board.PIRANHA_STATE_PATHS = [
                    reports / "coinbase_spot_shadow_dogeusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_xrpusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_solusd_piranha_state.json",
                ]
                board.STANDALONE_IOTX_PATH = reports / "live_iotx_bb_reversion_state.json"
                payload = board.build_payload(now=datetime(2026, 4, 12, 16, 54, 0, tzinfo=timezone.utc))
            finally:
                board.REPORTS = old_reports
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.RAVE_LIVE_STATE_PATH = old_rave
                board.MULTI_COIN_PORTFOLIO_STATE_PATH = old_portfolio
                board.RSI_SCOREBOARD_PATH = old_rsi
                board.PIRANHA_STATE_PATHS = old_piranha
                board.STANDALONE_IOTX_PATH = old_iotx

        self.assertEqual(payload["key_lanes"][0]["lane"], "rave_rsi_mr_live_v2")
        self.assertEqual(payload["key_lanes"][0]["action"], "restore_live_immediately")
        self.assertIn("urgent restore lane", payload["leadership_read"][0])
        self.assertTrue(any(row["product_id"] == "DOGE-USD" and row["action"] == "keep_probe_running" for row in payload["key_lanes"]))
        self.assertTrue(any(row["product_id"] == "IOTX-USD" and row["status"] == "missing" for row in payload["key_lanes"]))
        self.assertTrue(any(row["product_id"] == "VVV-USD" and row["action"] == "promote_small_live" for row in payload["rsi_shadow_queue"]))
        self.assertTrue(any(row["product_id"] == "PRL-USD" and row["action"] == "verify_then_promote" for row in payload["rsi_shadow_queue"]))

    def test_runtime_board_recognizes_active_rave_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "rave_rsi_mr_live_v2_state.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-04-12T17:29:50+00:00",
                        "state": {
                            "realized_net": 131.3795,
                            "closes": 17,
                            "position": {"hold": 17, "tp": 3.924375},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (reports / "multi_coin_portfolio_state.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-04-12T17:20:00+00:00",
                        "portfolio_realized": -6.9972,
                        "portfolio_closes": 13,
                        "portfolio_wr": 46.2,
                        "total_starting_cash": 500.0,
                    }
                ),
                encoding="utf-8",
            )
            (reports / "coinbase_spot_rsi_scoreboard.csv").write_text(
                "lane_name,product_id,readiness_verdict,baseline_72h_net_usd,walkforward,realized_net_usd,realized_closes,in_position,cash_usd,total_fees,signals_generated,heartbeat_age_seconds,pid,state_path,note\n",
                encoding="utf-8",
            )
            for product_id, filename in [
                ("DOGE-USD", "coinbase_spot_shadow_dogeusd_piranha_state.json"),
                ("XRP-USD", "coinbase_spot_shadow_xrpusd_piranha_state.json"),
                ("SOL-USD", "coinbase_spot_shadow_solusd_piranha_state.json"),
            ]:
                (reports / filename).write_text(
                    json.dumps(
                        {
                            "metadata": {"product_id": product_id},
                            "runner": {"heartbeat_at": "2026-04-12T17:20:00+00:00"},
                            "symbols": {product_id: {"realized_net_usd": 0.0, "realized_closes": 0, "open_lots": [], "cash_usd": 48.0}},
                        }
                    ),
                    encoding="utf-8",
                )

            old_reports = board.REPORTS
            old_md = board.MD_PATH
            old_json = board.JSON_PATH
            old_rave = board.RAVE_LIVE_STATE_PATH
            old_portfolio = board.MULTI_COIN_PORTFOLIO_STATE_PATH
            old_rsi = board.RSI_SCOREBOARD_PATH
            old_piranha = board.PIRANHA_STATE_PATHS
            old_iotx = board.STANDALONE_IOTX_PATH
            try:
                board.REPORTS = reports
                board.MD_PATH = reports / "out.md"
                board.JSON_PATH = reports / "out.json"
                board.RAVE_LIVE_STATE_PATH = reports / "rave_rsi_mr_live_v2_state.json"
                board.MULTI_COIN_PORTFOLIO_STATE_PATH = reports / "multi_coin_portfolio_state.json"
                board.RSI_SCOREBOARD_PATH = reports / "coinbase_spot_rsi_scoreboard.csv"
                board.PIRANHA_STATE_PATHS = [
                    reports / "coinbase_spot_shadow_dogeusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_xrpusd_piranha_state.json",
                    reports / "coinbase_spot_shadow_solusd_piranha_state.json",
                ]
                board.STANDALONE_IOTX_PATH = reports / "live_iotx_bb_reversion_state.json"
                payload = board.build_payload(now=datetime(2026, 4, 12, 17, 30, 0, tzinfo=timezone.utc))
            finally:
                board.REPORTS = old_reports
                board.MD_PATH = old_md
                board.JSON_PATH = old_json
                board.RAVE_LIVE_STATE_PATH = old_rave
                board.MULTI_COIN_PORTFOLIO_STATE_PATH = old_portfolio
                board.RSI_SCOREBOARD_PATH = old_rsi
                board.PIRANHA_STATE_PATHS = old_piranha
                board.STANDALONE_IOTX_PATH = old_iotx

        self.assertEqual(payload["key_lanes"][0]["lane"], "rave_rsi_mr_live_v2")
        self.assertEqual(payload["key_lanes"][0]["action"], "monitor_open_position")
        self.assertIn("active again", payload["leadership_read"][0])


if __name__ == "__main__":
    unittest.main()
