#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_graduation_gap_board as board


class CoinbaseSpotGraduationGapBoardTests(unittest.TestCase):
    def test_collect_missing_proofs_for_probationary_live_candidate(self) -> None:
        row = {
            "runtime_lane": "rave_rsi_mr_live_v2",
            "forward_lane": "shadow_coinbase_raveusd_rsi7",
            "runtime_closes": 18,
            "forward_closes": 24,
            "forward_readiness": "probationary",
            "runtime_realized_usd": 174.4342,
        }

        gaps = board.collect_missing_proofs(row)

        self.assertNotIn("create_forward_supervision_lane", gaps)
        self.assertNotIn("grow_runtime_closes_to_10", gaps)
        self.assertIn("grow_runtime_closes_to_20", gaps)
        self.assertIn("extend_forward_closes_to_30", gaps)
        self.assertIn("raise_forward_readiness_to_graduation_ready", gaps)

    def test_collect_missing_proofs_for_bench_only_candidate(self) -> None:
        row = {
            "lane": "",
            "runtime_closes": 0,
            "forward_closes": 0,
            "forward_readiness": "",
            "runtime_realized_usd": 0.0,
        }

        gaps = board.collect_missing_proofs(row)

        self.assertIn("create_forward_supervision_lane", gaps)
        self.assertIn("grow_runtime_closes_to_10", gaps)
        self.assertIn("grow_runtime_closes_to_20", gaps)
        self.assertIn("collect_forward_closes_to_30", gaps)
        self.assertIn("recover_positive_runtime_realized", gaps)

    def test_build_row_sets_priority_from_graduation_status(self) -> None:
        row = {
            "coin": "RAVE-USD",
            "strategy": "rsi_mr",
            "graduation_status": "micro_allocation_candidate",
            "runtime_lane": "rave_rsi_mr_live_v2",
            "forward_lane": "shadow_coinbase_raveusd_rsi7",
            "runtime_closes": 18,
            "forward_closes": 24,
            "forward_readiness": "probationary",
            "runtime_realized_usd": 174.4342,
            "forward_realized_usd": 5.5118,
            "reconciliation_net_30d_usd": 204.72,
        }

        built = board.build_row(row)

        self.assertEqual(built["priority"], "now")
        self.assertEqual(built["next_gate"], "full_graduation")
        self.assertEqual(built["missing_proof_count"], 3)


if __name__ == "__main__":
    unittest.main()
