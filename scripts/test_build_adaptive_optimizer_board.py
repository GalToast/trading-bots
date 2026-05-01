import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_adaptive_optimizer_board import build_payload


class AdaptiveOptimizerBoardTests(unittest.TestCase):
    def test_expected_trust_levels_present(self) -> None:
        payload = build_payload()
        rows = {row["surface_id"]: row for row in payload["rows"]}

        self.assertEqual(rows["allocation_optimizer"]["trust_level"], "reconcile_first")
        self.assertEqual(rows["optimal_portfolio_optimizer"]["trust_level"], "reconcile_first")
        self.assertEqual(rows["atr_step_optimizer"]["trust_level"], "shadow_only")
        self.assertEqual(rows["m5_kelly_optimizer_v2"]["trust_level"], "research_reference")

    def test_summary_counts(self) -> None:
        payload = build_payload()
        counts = payload["summary"]["counts_by_trust_level"]

        self.assertEqual(counts["reconcile_first"], 2)
        self.assertEqual(counts["shadow_only"], 3)
        self.assertEqual(counts["research_reference"], 1)


if __name__ == "__main__":
    unittest.main()
