import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_optimizer_reconciliation_board as board


class AdaptiveOptimizerReconciliationBoardTests(unittest.TestCase):
    def test_expected_surface_statuses(self) -> None:
        payload = board.build_payload()
        rows = {row["surface_id"]: row for row in payload["rows"]}

        self.assertEqual(rows["allocation_optimizer"]["status"], "reconciled_divergent")
        self.assertEqual(rows["optimal_portfolio_optimizer"]["status"], "reconciled_divergent")
        self.assertEqual(rows["benchmark_engine_reference"]["status"], "aligned_reference")

    def test_benchmark_reference_has_alignment_summary(self) -> None:
        payload = board.build_payload()
        rows = {row["surface_id"]: row for row in payload["rows"]}
        summary = rows["benchmark_engine_reference"]["alignment_summary"]

        self.assertTrue(summary["available"])
        self.assertTrue(summary["all_zero_deltas"])

    def test_replayed_surfaces_have_reconciliation_summaries(self) -> None:
        payload = board.build_payload()
        rows = {row["surface_id"]: row for row in payload["rows"]}

        allocation_summary = rows["allocation_optimizer"]["reconciliation_summary"]
        optimal_summary = rows["optimal_portfolio_optimizer"]["reconciliation_summary"]

        self.assertTrue(allocation_summary["available"])
        self.assertIn("equal_split", allocation_summary["collapsed_plans"])
        self.assertEqual(allocation_summary["source_mode"], "native_inline_replay")
        self.assertTrue(optimal_summary["available"])
        self.assertIn("per_coin_5_33", optimal_summary["collapsed_scenarios"])
        self.assertEqual(optimal_summary["source_mode"], "native_inline_replay")


if __name__ == "__main__":
    unittest.main()
