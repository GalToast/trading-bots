#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_same_coin_stack_admission_board as board


class CoinbaseSameCoinStackAdmissionBoardTests(unittest.TestCase):
    def test_build_row_promotes_overlap_verified_nom_case(self) -> None:
        stack_row = {
            "coin": "NOM-USD",
            "stack_policy": "parallel_shadows_allowed",
            "preferred_primary_lane": "range_breakout_shadow",
            "max_live_lanes": 2,
            "lane_summaries": [
                {"strategy": "range_breakout_shadow"},
                {"strategy": "momentum_registry_validation"},
            ],
        }
        overlap_map = {
            "NOM-USD": {
                "coin": "NOM-USD",
                "combined": {"total_pnl": 4681.04, "total_trades": 411},
                "momentum": {"total_pnl": 1314.83},
                "range_breakout": {"total_pnl": 3366.21},
                "overlap_analysis": {"1bar_5min": {"overlap_pct": 33.4}},
            }
        }

        row = board.build_row(stack_row, overlap_map)

        self.assertEqual(row["admission_decision"], "allow_dual_shadow_stack")
        self.assertEqual(row["admission_status"], "overlap_verified")
        self.assertEqual(row["recommended_max_live_lanes"], 2)
        self.assertEqual(row["overlap_pct_5m"], 33.4)
        self.assertEqual(row["combined_uplift_vs_best_single"], 1314.83)

    def test_build_row_requires_overlap_for_parallel_shadow_case(self) -> None:
        stack_row = {
            "coin": "SUP-USD",
            "stack_policy": "parallel_shadows_allowed",
            "preferred_primary_lane": "range_breakout_shadow",
            "max_live_lanes": 2,
            "lane_summaries": [
                {"strategy": "range_breakout_shadow"},
                {"strategy": "momentum_registry_validation"},
            ],
        }

        row = board.build_row(stack_row, {})

        self.assertEqual(row["admission_decision"], "require_overlap_check_before_dual_live")
        self.assertEqual(row["admission_status"], "shadow_only_pending_overlap")
        self.assertEqual(row["recommended_max_live_lanes"], 1)

    def test_build_row_keeps_runtime_proven_dual_live(self) -> None:
        stack_row = {
            "coin": "RAVE-USD",
            "stack_policy": "dual_live_allowed",
            "preferred_primary_lane": "mom_10",
            "max_live_lanes": 2,
            "lane_summaries": [
                {"strategy": "mom_10"},
                {"strategy": "rsi_mean_reversion_active"},
            ],
        }

        row = board.build_row(stack_row, {})

        self.assertEqual(row["admission_decision"], "keep_dual_live")
        self.assertEqual(row["admission_status"], "runtime_proven")
        self.assertEqual(row["overlap_evidence_status"], "not_required_live_proven")


if __name__ == "__main__":
    unittest.main()
