from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import regime_classification_live as regime


class RegimeClassificationLiveTests(unittest.TestCase):
    def test_analyze_symbol_emits_range_atr_metrics(self) -> None:
        closes = [100.0 + idx * 0.75 for idx in range(40)]
        highs = [close + 2.0 for close in closes]
        lows = [close - 1.0 for close in closes]

        result = regime.analyze_symbol(
            "BTCUSD",
            {
                "open": closes,
                "high": highs,
                "low": lows,
                "close": closes,
            },
        )

        self.assertIn("avg_range", result)
        self.assertIn("range_atr_ratio", result)
        self.assertIn("range_atr_clamped_coeff", result)
        self.assertIn("range_atr_formula_step", result)
        self.assertGreater(result["avg_range"], 0.0)
        self.assertGreater(result["range_atr_ratio"], 0.0)
        self.assertGreater(result["range_atr_formula_step"], 0.0)

        expected_coeff = max(0.5, min(1.2, 1.6 - 0.6 * result["range_atr_ratio"]))
        self.assertAlmostEqual(result["range_atr_clamped_coeff"], round(expected_coeff, 5), places=5)
        expected_step = round(result["avg_range"] * result["range_atr_clamped_coeff"], 5)
        self.assertAlmostEqual(result["range_atr_formula_step"], expected_step, places=5)


if __name__ == "__main__":
    unittest.main()
