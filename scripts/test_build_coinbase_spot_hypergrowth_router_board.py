#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_hypergrowth_router_board as board


class CoinbaseSpotHypergrowthRouterBoardTests(unittest.TestCase):
    def test_infer_family_maps_known_lane_names(self) -> None:
        self.assertEqual(board.infer_family("mom_10"), "momentum")
        self.assertEqual(board.infer_family("range_breakout_shadow"), "range_breakout")
        self.assertEqual(board.infer_family("shadow_coinbase_raveusd_rsi7"), "rsi_mean_reversion")
        self.assertEqual(board.infer_family("coinbase_spot_piranha"), "spot_piranha")

    def test_select_primary_row_prefers_stack_primary_when_present(self) -> None:
        rows = [
            {"strategy": "mom_10", "priority_score": 700.0},
            {"strategy": "range_breakout_shadow", "priority_score": 1200.0},
        ]

        selected = board.select_primary_row(rows, "mom_10")

        self.assertIsNotNone(selected)
        self.assertEqual(selected["strategy"], "mom_10")

    def test_router_tier_promotes_live_and_high_score_names(self) -> None:
        self.assertEqual(board.router_tier("maintain_live", 100.0, 2), "active_core")
        self.assertEqual(board.router_tier("launch_after_wave_1", 800.0, 1), "hypergrowth_core")
        self.assertEqual(board.router_tier("launch_after_wave_1", 100.0, 2), "stack_candidate")
        self.assertEqual(board.router_tier("watch_only", 50.0, 1), "watchlist")


if __name__ == "__main__":
    unittest.main()
