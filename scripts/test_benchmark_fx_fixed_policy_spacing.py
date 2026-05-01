from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_fx_fixed_policy_spacing import build_markdown


class FixedPolicySpacingTests(unittest.TestCase):
    def test_build_markdown_mentions_baseline_and_best(self) -> None:
        markdown = build_markdown(
            [
                {
                    "symbol": "GBPUSD",
                    "policy": "allprof_gap1_alpha50",
                    "step_pips": "2.0",
                    "variant_combined_usd": "90",
                    "delta_vs_validated_step": "0",
                    "is_validated_default": "1",
                },
                {
                    "symbol": "GBPUSD",
                    "policy": "allprof_gap1_alpha50",
                    "step_pips": "2.5",
                    "variant_combined_usd": "110",
                    "delta_vs_validated_step": "20",
                    "is_validated_default": "0",
                },
                {
                    "symbol": "EURUSD",
                    "policy": "outer_gap2_alpha50",
                    "step_pips": "3.0",
                    "variant_combined_usd": "70",
                    "delta_vs_validated_step": "0",
                    "is_validated_default": "1",
                },
            ]
        )
        self.assertIn("# FX Fixed-Policy Spacing Ladder", markdown)
        self.assertIn("best step is `2.5`", markdown)
        self.assertIn("validated-step fixed-policy basket", markdown)


if __name__ == "__main__":
    unittest.main()
