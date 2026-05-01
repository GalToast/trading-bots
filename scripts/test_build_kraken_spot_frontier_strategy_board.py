#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_spot_frontier_strategy_board as board


class KrakenSpotFrontierStrategyBoardTests(unittest.TestCase):
    def test_extreme_spread_mer_candidate_is_vetoed_from_runner_feed(self) -> None:
        old_lookup = dict(board.OPPORTUNITY_LOOKUP)
        try:
            board.OPPORTUNITY_LOOKUP.clear()
            board.OPPORTUNITY_LOOKUP["BMB-USD"] = {"mer": 1.7}
            row = board.build_strategy_row(
                {
                    "product_id": "BMB-USD",
                    "kraken_edge_bps": 0,
                    "best_move_bps": 600,
                    "spread_bps": board.MAX_MAKER_HARVEST_SPREAD_BPS + 1,
                    "verdict": "geometric_alpha",
                },
                1,
            )
        finally:
            board.OPPORTUNITY_LOOKUP.clear()
            board.OPPORTUNITY_LOOKUP.update(old_lookup)

        self.assertEqual(row["playbook"], "maker_harvest_extreme_spread_veto")
        self.assertLess(row["machinegun_score"], 0)

    def test_nut_cracker_threshold_is_explicit_and_consistent(self) -> None:
        row = board.build_strategy_row(
            {
                "product_id": "CQT-USD",
                "kraken_edge_bps": 100,
                "best_move_bps": 100,
                "spread_bps": 10,
                "tail_prob": board.NUT_CRACKER_THRESHOLD,
                "fast_green_prob": board.NUT_CRACKER_THRESHOLD,
                "verdict": "geometric_alpha",
            },
            1,
        )

        self.assertEqual(row["nut_cracker_verdict"], "NUT_CRACKER_PRIME")
        payload = board.build_payload()
        self.assertIn(f"{board.NUT_CRACKER_THRESHOLD:.2f}", payload["leadership_read"][-1])


if __name__ == "__main__":
    unittest.main()
