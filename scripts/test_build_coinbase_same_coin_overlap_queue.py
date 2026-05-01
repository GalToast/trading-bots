#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_same_coin_overlap_queue as queue_builder


class CoinbaseSameCoinOverlapQueueTests(unittest.TestCase):
    def test_build_pending_row_marks_cautious_secondary(self) -> None:
        admission_row = {
            "coin": "BAL-USD",
            "current_stack_policy": "parallel_shadows_allowed_cautious",
            "preferred_primary_lane": "range_breakout_shadow",
            "reason": "needs overlap proof",
        }
        stack_row = {
            "preferred_primary_lane": "range_breakout_shadow",
            "lane_summaries": [
                {
                    "strategy": "range_breakout_shadow",
                    "reconciliation_30d_net_usd": 47.16,
                    "reconciliation_30d_closes": 30,
                    "router_decision": "breakout_shadow_candidate",
                },
                {
                    "strategy": "mom_50",
                    "reconciliation_30d_net_usd": 36.59,
                    "reconciliation_30d_closes": 32,
                    "router_decision": "reconcile_first",
                },
            ],
        }

        row = queue_builder.build_pending_row(admission_row, stack_row)

        self.assertEqual(row["priority"], "medium")
        self.assertTrue(row["router_caution"])
        self.assertEqual(row["candidate_secondary_lane"], "mom_50")
        self.assertEqual(row["combined_strength_score"], 83.75)

    def test_build_leadership_read_prefers_top_pending_and_completed_benchmark(self) -> None:
        pending_rows = [
            {
                "coin": "SUP-USD",
                "combined_strength_score": 236.45,
                "router_caution": False,
            },
            {
                "coin": "BAL-USD",
                "combined_strength_score": 83.75,
                "router_caution": True,
            },
        ]
        completed_rows = [
            {
                "coin": "NOM-USD",
                "overlap_pct_5m": 33.4,
                "combined_uplift_vs_best_single": 1314.83,
            }
        ]

        lines = queue_builder.build_leadership_read(pending_rows, completed_rows)

        self.assertTrue(any("NOM" in line for line in lines))
        self.assertTrue(any("SUP" in line for line in lines))
        self.assertTrue(any("BAL" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
