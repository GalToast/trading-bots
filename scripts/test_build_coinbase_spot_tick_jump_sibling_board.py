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

import build_coinbase_spot_tick_jump_sibling_board as board


class CoinbaseSpotTickJumpSiblingBoardTests(unittest.TestCase):
    def test_mog_like_one_tick_candidate_clears_fee_wall(self) -> None:
        candles = []
        for idx in range(12):
            price = 0.00000015 if idx % 3 else 0.00000016
            candles.append({"start": idx * 60, "open": price, "high": price, "low": 0.00000015, "close": price, "volume": 1000})
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache = root / "cache.json"
            pulse = root / "pulse.json"
            radar = root / "radar.json"
            cache.write_text(
                json.dumps({"entries": {"MOG-USD|ONE_MINUTE|3h": {"product_id": "MOG-USD", "candles": candles}}}),
                encoding="utf-8",
            )
            pulse.write_text(json.dumps({"rows": [{"product_id": "MOG-USD", "price": 0.00000015, "spread_bps": 10, "quote_volume_native": 1000000}]}), encoding="utf-8")
            radar.write_text(json.dumps({"rows": [{"product_id": "MOG-USD", "spread_bps": 10, "signal_state": "live_hot"}]}), encoding="utf-8")

            rows = board.build_rows(cache_path=cache, pulse_path=pulse, radar_path=radar, fee_bps_per_side=120, target_net_pct=0.5, lookahead_bars=3)

        self.assertEqual(rows[0]["product_id"], "MOG-USD")
        self.assertEqual(rows[0]["verdict"], "mog_like_tick_jump_candidate")
        self.assertGreater(rows[0]["net_one_step_after_hurdle_pct"], 0)

    def test_wide_spread_candidate_is_rejected(self) -> None:
        candles = [
            {"open": 1.0, "high": 1.4, "low": 1.0, "close": 1.4 if idx % 2 else 1.0, "volume": 1}
            for idx in range(12)
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache = root / "cache.json"
            pulse = root / "pulse.json"
            radar = root / "radar.json"
            cache.write_text(json.dumps({"entries": {"WIDE-USD|ONE_MINUTE|3h": {"product_id": "WIDE-USD", "candles": candles}}}), encoding="utf-8")
            pulse.write_text(json.dumps({"rows": [{"product_id": "WIDE-USD", "price": 1.0, "spread_bps": 500}]}), encoding="utf-8")
            radar.write_text(json.dumps({"rows": []}), encoding="utf-8")

            rows = board.build_rows(cache_path=cache, pulse_path=pulse, radar_path=radar, max_spread_bps=100)

        self.assertEqual(rows[0]["verdict"], "reject_wide_spread")


if __name__ == "__main__":
    unittest.main()
