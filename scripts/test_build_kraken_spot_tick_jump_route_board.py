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

import build_kraken_spot_tick_jump_route_board as board


class KrakenSpotTickJumpRouteBoardTests(unittest.TestCase):
    def test_fee_flip_candidate_when_kraken_clears_and_coinbase_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            siblings = root / "siblings.json"
            radar = root / "radar.json"
            siblings.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "product_id": "KAT-USD",
                                "verdict": "movement_candidate_needs_multi_tick",
                                "score": 10,
                                "spread_bps": 10,
                                "best_forward_close_pct": 2.0,
                                "fee_clear_close_hit_rate_pct": 5.0,
                                "fee_clear_high_hit_rate_pct": 8.0,
                                "observed_step_pct": 0.1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            radar.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "product_id": "KAT-USD",
                                "quote_currency": "USD",
                                "ret_5m_bps": 180,
                                "spread_bps": 10,
                                "samples": 3,
                                "can_trade_starting_cash": True,
                                "min_notional_usd": 5,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            rows = board.build_rows(coinbase_sibling_path=siblings, kraken_radar_path=radar, target_net_pct=0.5)

        self.assertEqual(rows[0]["route_verdict"], "kraken_fee_flip_candidate")
        self.assertEqual(rows[0]["kraken_product_id"], "KAT-USD")
        self.assertGreater(rows[0]["kraken_edge_bps"], 0)
        self.assertLess(rows[0]["coinbase_edge_bps"], 0)

    def test_missing_route_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            siblings = root / "siblings.json"
            radar = root / "radar.json"
            siblings.write_text(
                json.dumps({"rows": [{"product_id": "NOPE-USD", "verdict": "movement_candidate_needs_multi_tick"}]}),
                encoding="utf-8",
            )
            radar.write_text(json.dumps({"rows": []}), encoding="utf-8")

            rows = board.build_rows(coinbase_sibling_path=siblings, kraken_radar_path=radar)

        self.assertEqual(rows[0]["route_verdict"], "missing_kraken_radar_route")


if __name__ == "__main__":
    unittest.main()
