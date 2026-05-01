#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hungry_hippo_personality_selector as selector


class HungryHippoPersonalitySelectorTests(unittest.TestCase):
    def test_override_personality_uses_override_template_not_only_label(self) -> None:
        config = selector.compute_personality_config("US30", "mixed_hold", 10.0)

        self.assertEqual(config["personality"], "BREAKOUT")
        self.assertEqual(config["alpha"], selector.PERSONALITIES["BREAKOUT"]["alpha"])
        self.assertEqual(config["max_open_per_side"], selector.PERSONALITIES["BREAKOUT"]["max_open_per_side"])
        self.assertEqual(config["rearm_variant"], selector.PERSONALITIES["BREAKOUT"]["rearm_variant"])

    def test_seeded_rows_get_generic_seed_note(self) -> None:
        config = selector.compute_personality_config(
            "USDCHF",
            "bounce_reversal",
            0.00026,
            regime_data={"consensus": "seeded_policy"},
        )

        self.assertIn("seeded", config["notes"].lower())

    def test_build_symbol_configs_covers_all_available_regime_rows(self) -> None:
        results = selector.build_symbol_configs(
            {
                "rows": [
                    {
                        "symbol": "AUDUSD",
                        "control_mode": "wait_extreme_confirmation",
                        "computed_buy_step": 0.00045,
                        "buy_step_coeff": 1.5,
                        "computed_sell_step": 0.00036,
                        "sell_step_coeff": 1.2,
                    }
                ]
            },
            {"symbols": []},
        )

        self.assertIn("AUDUSD", results)
        self.assertEqual(results["AUDUSD"]["personality"], "CHOP_AGGRESSIVE")


if __name__ == "__main__":
    unittest.main()
