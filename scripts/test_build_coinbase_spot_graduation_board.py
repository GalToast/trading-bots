#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_graduation_board as board


class CoinbaseSpotGraduationBoardTests(unittest.TestCase):
    def test_load_forward_review_rows_parses_markdown_table(self) -> None:
        original_path = board.RSI_FORWARD_REVIEW_MD_PATH
        temp_path = Path(__file__).with_name("tmp_forward_review.md")
        try:
            temp_path.write_text(
                "# Coinbase Spot RSI Forward Review\n\n"
                "| Product | Lane | Readiness | Forward Status | Baseline 72h $ | Realized $ | Delta vs Baseline $ | Ratio | Closes | In Pos | Cash $ | Heartbeat Age (s) | Note |\n"
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n"
                "| RAVE-USD | shadow_coinbase_raveusd_rsi7 | probationary | holding_up | 7.3999 | 5.5118 | -1.8881 | 0.7448 | 24 | 0 | 52.96 | 5.4 | enough closes |\n",
                encoding="utf-8",
            )
            board.RSI_FORWARD_REVIEW_MD_PATH = temp_path
            rows = board.load_forward_review_rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["product_id"], "RAVE-USD")
            self.assertEqual(rows[0]["closes"], 24)
            self.assertAlmostEqual(rows[0]["ratio"], 0.7448)
        finally:
            board.RSI_FORWARD_REVIEW_MD_PATH = original_path
            if temp_path.exists():
                temp_path.unlink()

    def test_decide_graduation_status_keeps_probationary_row_below_full_clear(self) -> None:
        evidence_row = {"verdict": "deployable_priority"}
        runtime_row = {"realized_net_usd": 131.3795, "closes": 17, "status": "active"}
        forward_row = {"readiness": "probationary", "forward_status": "holding_up", "closes": 24}
        deploy_row = {"action": "promote_small_live"}

        status, reason = board.decide_graduation_status(evidence_row, runtime_row, forward_row, deploy_row)

        self.assertEqual(status, "micro_allocation_candidate")
        self.assertIn("below full graduation bar", reason)

    def test_choose_forced_nominee_prefers_micro_candidate(self) -> None:
        rows = [
            {"coin": "A8-USD", "graduation_status": "shadow_only", "graduation_score": 40.0},
            {"coin": "RAVE-USD", "graduation_status": "micro_allocation_candidate", "graduation_score": 100.0},
            {"coin": "BAL-USD", "graduation_status": "needs_forward_proof", "graduation_score": 90.0},
        ]

        nominee = board.choose_forced_nominee(rows)

        self.assertIsNotNone(nominee)
        self.assertEqual(nominee["coin"], "RAVE-USD")

    def test_build_candidate_row_prefers_runtime_board_values_over_stale_evidence(self) -> None:
        evidence_row = {
            "combo_id": "rave_rsi_mr",
            "coin": "RAVE-USD",
            "strategy": "rsi_mr",
            "family": "rsi_mean_reversion",
            "verdict": "deployable_priority",
            "reconciliation_net_30d_usd": 204.72,
            "reconciliation_closes_30d": 75,
            "library_sweep_partial_14d_net_usd": 386.27,
            "library_sweep_partial_14d_closes": 36,
            "runtime_realized_usd": 131.3795,
            "runtime_closes": 17,
            "deployability_action": "promote_small_live",
        }
        runtime_map = {
            ("RAVE-USD", "rave_rsi_mr_live_v2"): {
                "realized_net_usd": 174.4342,
                "closes": 18,
                "status": "offline",
            }
        }
        deploy_map = {
            ("RAVE-USD", "shadow_coinbase_raveusd_rsi7"): {
                "action": "promote_small_live",
            }
        }
        forward_rows = [
            {
                "product_id": "RAVE-USD",
                "lane": "shadow_coinbase_raveusd_rsi7",
                "readiness": "probationary",
                "forward_status": "holding_up",
                "realized_usd": 5.5118,
                "ratio": 0.7448,
                "closes": 24,
            }
        ]

        built = board.build_candidate_row(evidence_row, runtime_map, deploy_map, forward_rows)

        self.assertEqual(built["runtime_closes"], 18)
        self.assertAlmostEqual(built["runtime_realized_usd"], 174.4342)
        self.assertEqual(built["runtime_status"], "offline")
        self.assertEqual(built["runtime_lane"], "rave_rsi_mr_live_v2")
        self.assertEqual(built["forward_lane"], "shadow_coinbase_raveusd_rsi7")


if __name__ == "__main__":
    unittest.main()
