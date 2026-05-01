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

import live_coinbase_spot_piranha_bundle_shadow as bundle


class CoinbaseSpotPiranhaBundleTests(unittest.TestCase):
    def test_load_bundle_config_filters_requested_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "bundle.json"
            config_path.write_text(
                json.dumps(
                    {
                        "poll_seconds": 5,
                        "lanes": [
                            {
                                "lane_name": "lane_xrp",
                                "product_id": "XRP-USD",
                                "state_path": "reports/xrp_state.json",
                                "event_path": "reports/xrp_events.jsonl",
                                "buy_step": 0.015,
                                "profit_target": 0.025,
                            },
                            {
                                "lane_name": "lane_doge",
                                "product_id": "DOGE-USD",
                                "state_path": "reports/doge_state.json",
                                "event_path": "reports/doge_events.jsonl",
                                "buy_step": 0.0013,
                                "profit_target": 0.0018,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            lanes = bundle.load_bundle_config(config_path, lane_names={"lane_doge"})

        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0].lane_name, "lane_doge")
        self.assertEqual(lanes[0].product_id, "DOGE-USD")

    def test_run_lane_once_updates_separate_lane_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lane_xrp = bundle.BundleLane(
                lane_name="shadow_coinbase_xrpusd_piranha",
                product_id="XRP-USD",
                timeframe="M1",
                buy_step=0.015,
                profit_target=0.025,
                quote_per_buy=6.0,
                starting_cash=48.0,
                max_lots=6,
                taker_fee_bps=60.0,
                min_hold_seconds=0,
                poll_seconds=5.0,
                state_path=root / "xrp_state.json",
                event_path=root / "xrp_events.jsonl",
            )
            lane_doge = bundle.BundleLane(
                lane_name="shadow_coinbase_dogeusd_piranha",
                product_id="DOGE-USD",
                timeframe="M1",
                buy_step=0.0013,
                profit_target=0.0018,
                quote_per_buy=6.0,
                starting_cash=48.0,
                max_lots=6,
                taker_fee_bps=60.0,
                min_hold_seconds=0,
                poll_seconds=5.0,
                state_path=root / "doge_state.json",
                event_path=root / "doge_events.jsonl",
            )

            class FakeClient:
                def get_product(self, product_id: str) -> dict[str, str]:
                    return {"product_type": "SPOT", "display_name": product_id}

            client = FakeClient()
            runtime_xrp = bundle.build_runtime(lane_xrp, client)
            runtime_doge = bundle.build_runtime(lane_doge, client)

            tick_map = {
                "XRP-USD": {"time": 1, "time_msc": 1000, "bid": 1.00, "ask": 1.01},
                "DOGE-USD": {"time": 2, "time_msc": 2000, "bid": 0.20, "ask": 0.21},
            }

            original_fetch = bundle.fetch_coinbase_tick
            try:
                bundle.fetch_coinbase_tick = lambda client, product_id: tick_map[product_id]
                bundle.bootstrap_runtime(client, runtime_xrp)
                bundle.bootstrap_runtime(client, runtime_doge)
                bundle.run_lane_once(client, runtime_xrp)
                bundle.run_lane_once(client, runtime_doge)
            finally:
                bundle.fetch_coinbase_tick = original_fetch

            payload_xrp = json.loads(lane_xrp.state_path.read_text(encoding="utf-8"))
            payload_doge = json.loads(lane_doge.state_path.read_text(encoding="utf-8"))

        self.assertEqual(payload_xrp["metadata"]["product_id"], "XRP-USD")
        self.assertEqual(payload_xrp["runner"]["lane_name"], "shadow_coinbase_xrpusd_piranha")
        self.assertEqual(payload_xrp["runner"]["pid"], payload_doge["runner"]["pid"])
        self.assertIn("XRP-USD", payload_xrp["symbols"])
        self.assertEqual(payload_doge["metadata"]["product_id"], "DOGE-USD")
        self.assertEqual(payload_doge["runner"]["lane_name"], "shadow_coinbase_dogeusd_piranha")
        self.assertIn("DOGE-USD", payload_doge["symbols"])


if __name__ == "__main__":
    unittest.main()
