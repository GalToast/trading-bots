from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_fx_fixed_step_close_policy import build_markdown, select_close_positions


class FixedStepClosePolicyTests(unittest.TestCase):
    def test_select_close_positions(self) -> None:
        self.assertEqual(select_close_positions(4, 2, "outer"), [0])
        self.assertEqual(select_close_positions(4, 2, "inner"), [1])
        self.assertEqual(select_close_positions(4, 2, "all_profitable", [0, 2, 3]), [0, 2, 3])
        self.assertEqual(select_close_positions(2, 2, "outer"), [])

    def test_build_markdown_mentions_best_rows(self) -> None:
        markdown = build_markdown(
            rows=[
                {
                    "symbol": "GBPUSD",
                    "policy": "outer_gap1_alpha50",
                    "close_alpha": "0.5",
                    "variant_combined_usd": "20",
                    "baseline_combined_usd": "10",
                    "delta_combined_usd": "10",
                    "variant_closes": "100",
                    "close_events": "90",
                    "variant_max_open": "22",
                },
                {
                    "symbol": "EURUSD",
                    "policy": "outer_gap2_alpha0",
                    "close_alpha": "0.0",
                    "variant_combined_usd": "12",
                    "baseline_combined_usd": "11",
                    "delta_combined_usd": "1",
                    "variant_closes": "50",
                    "close_events": "40",
                    "variant_max_open": "22",
                },
            ],
            summary_rows=[
                {
                    "policy": "outer_gap1_alpha100",
                    "baseline_total_usd": "21",
                    "variant_total_usd": "32",
                    "delta_total_usd": "11",
                    "close_alpha": "1.0",
                },
                {
                    "policy": "outer_gap2_alpha50",
                    "baseline_total_usd": "21",
                    "variant_total_usd": "28",
                    "delta_total_usd": "7",
                    "close_alpha": "0.5",
                }
            ],
        )
        self.assertIn("# FX Fixed-Step Close Policy Ladder", markdown)
        self.assertIn("Best practical mid-fill basket policy", markdown)
        self.assertIn("outer_gap2_alpha50", markdown)
        self.assertIn("Use this ladder before touching FX spacing", markdown)


if __name__ == "__main__":
    unittest.main()
