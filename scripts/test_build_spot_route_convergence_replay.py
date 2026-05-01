#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_spot_route_convergence_replay as replay


class FakeKrakenClient:
    def asset_pairs(self) -> dict[str, Any]:
        return {
            "AAAUSD": {
                "altname": "AAAUSD",
                "wsname": "AAA/USD",
                "ordermin": "1",
                "costmin": "1",
                "pair_decimals": 4,
                "lot_decimals": 8,
                "status": "online",
            }
        }


def write_cache(path: Path, samples: dict[str, list[dict[str, float]]]) -> None:
    path.write_text(json.dumps({"samples": samples, "updated_at": "test"}, indent=2), encoding="utf-8")


class SpotRouteConvergenceReplayTests(unittest.TestCase):
    def test_single_leg_entry_future_exit_can_be_price_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                {
                    "AAAUSD": [
                        {"ts": 1000.0, "bid": 1.0000, "ask": 1.0100},
                        {"ts": 1010.0, "bid": 1.0300, "ask": 1.0310},
                    ]
                },
            )

            payload = replay.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                numeraires={"USD"},
                quotes={"USD"},
                stable_assets={"USD"},
                horizons=[10.0],
                start_usd=50.0,
                taker_fee_bps=0.0,
                signal_fee_bps=0.0,
                min_signal_gap_bps=-200.0,
                min_net_bps=1.0,
                max_events=100,
            )

            self.assertGreater(payload["summary"]["net_positive_price_only"], 0)
            self.assertGreater(payload["summary"]["best_staged_net_bps"], 100.0)
            self.assertIn("depth_unavailable_in_radar_cache", payload["rows"][0]["blockers"])

    def test_fee_drag_blocks_flat_future_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                {
                    "AAAUSD": [
                        {"ts": 1000.0, "bid": 1.0000, "ask": 1.0100},
                        {"ts": 1010.0, "bid": 1.0100, "ask": 1.0110},
                    ]
                },
            )

            payload = replay.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                numeraires={"USD"},
                quotes={"USD"},
                stable_assets={"USD"},
                horizons=[10.0],
                start_usd=50.0,
                taker_fee_bps=40.0,
                signal_fee_bps=0.0,
                min_signal_gap_bps=-200.0,
                min_net_bps=1.0,
                max_events=100,
            )

            self.assertEqual(payload["summary"]["executable_positive"], 0)
            self.assertLess(payload["summary"]["best_staged_net_bps"], 0.0)
            self.assertIn("net_edge_below_threshold", payload["rows"][0]["blockers"])


if __name__ == "__main__":
    unittest.main()
