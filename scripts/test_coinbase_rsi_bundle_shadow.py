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

import live_coinbase_rsi_bundle_shadow as bundle


class CoinbaseRSIBundleShadowTests(unittest.TestCase):
    def test_load_bundle_config_filters_requested_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "bundle.json"
            config_path.write_text(
                json.dumps(
                    {
                        "poll_seconds": 30,
                        "lanes": [
                            {
                                "lane_name": "lane_a",
                                "product_id": "ARB-USD",
                                "state_path": "reports/a_state.json",
                                "event_path": "reports/a_events.jsonl",
                            },
                            {
                                "lane_name": "lane_b",
                                "product_id": "MOG-USD",
                                "state_path": "reports/b_state.json",
                                "event_path": "reports/b_events.jsonl",
                                "rsi_period": 4,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            lanes = bundle.load_bundle_config(config_path, lane_names={"lane_b"})

        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0].lane_name, "lane_b")
        self.assertEqual(lanes[0].product_id, "MOG-USD")
        self.assertEqual(lanes[0].rsi_period, 4)

    def test_run_lane_once_updates_separate_lane_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lane_a = bundle.BundleLane(
                lane_name="shadow_coinbase_arbusd_rsi7",
                product_id="ARB-USD",
                state_path=root / "arbusd_state.json",
                event_path=root / "arbusd_events.jsonl",
                rsi_period=7,
                oversold=30.0,
                overbought=70.0,
                profit_target_pct=0.02,
                stop_loss_pct=0.003,
                max_hold_bars=48,
                maker_fee_bps=5.0,
                deploy_pct=0.9,
                starting_cash=48.0,
                granularity="FIVE_MINUTE",
                poll_seconds=30.0,
            )
            lane_b = bundle.BundleLane(
                lane_name="shadow_coinbase_mogusd_rsi4",
                product_id="MOG-USD",
                state_path=root / "mogusd_state.json",
                event_path=root / "mogusd_events.jsonl",
                rsi_period=4,
                oversold=30.0,
                overbought=101.0,
                profit_target_pct=7.5,
                stop_loss_pct=0.5,
                max_hold_bars=24,
                maker_fee_bps=40.0,
                deploy_pct=0.95,
                starting_cash=48.0,
                granularity="FIVE_MINUTE",
                poll_seconds=30.0,
            )
            runtime_a = bundle.build_runtime(lane_a)
            runtime_b = bundle.build_runtime(lane_b)

            latest_map = {
                "ARB-USD": {"time": 100, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                "MOG-USD": {"time": 200, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 1.0},
            }

            original_fetch_latest_candle = bundle.fetch_latest_candle
            try:
                bundle.fetch_latest_candle = lambda client, product_id, granularity, event_logger=None: latest_map[product_id]
                bundle.run_lane_once(object(), runtime_a)
                bundle.run_lane_once(object(), runtime_b)
            finally:
                bundle.fetch_latest_candle = original_fetch_latest_candle

            payload_a = json.loads(lane_a.state_path.read_text(encoding="utf-8"))
            payload_b = json.loads(lane_b.state_path.read_text(encoding="utf-8"))

        self.assertEqual(payload_a["state"]["product_id"], "ARB-USD")
        self.assertEqual(payload_a["runner"]["lane_name"], "shadow_coinbase_arbusd_rsi7")
        self.assertEqual(payload_a["state"]["last_candle_time"], 100)
        self.assertEqual(payload_b["state"]["product_id"], "MOG-USD")
        self.assertEqual(payload_b["runner"]["lane_name"], "shadow_coinbase_mogusd_rsi4")
        self.assertEqual(payload_b["state"]["last_candle_time"], 200)
        self.assertEqual(payload_a["runner"]["pid"], payload_b["runner"]["pid"])


if __name__ == "__main__":
    unittest.main()
