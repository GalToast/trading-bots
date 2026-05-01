import unittest
from unittest.mock import MagicMock
import sys
from pathlib import Path

# Add scripts directory to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from structure_shapeshifter_bridge import check_and_adapt

class TestStructureRuntimeMapping(unittest.TestCase):
    def setUp(self):
        import structure_shapeshifter_bridge

        structure_shapeshifter_bridge._ADAPTATION_STATE.clear()

    def test_field_targeting(self):
        """Verify that check_and_adapt targets the correct engine fields."""
        # Mock engine
        engine = MagicMock()
        engine.symbol = "TEST_BTC"
        engine.base_step_buy_px = 100.0
        engine.base_step_sell_px = 100.0
        engine.close_alpha = 0.5
        engine.state = MagicMock()
        
        # Mock bars (enough to pass the len check)
        bars = [{"close": 10.0}] * 60
        
        # Mock the detector and mapper to return a deterministic flip
        import structure_shapeshifter_bridge
        structure_shapeshifter_bridge.detect_structure = MagicMock(return_value={
            "primary_structure": "bull_flag",
            "lattice_geometry": {
                "step_buy": 50.0,
                "step_sell": 150.0,
                "alpha": 0.8,
                "asymmetry_ratio": 0.33,
                "mode": "trending",
                "reason": "Bull flag detected"
            }
        })
        structure_shapeshifter_bridge.structure_to_geometry = MagicMock(return_value={
            "step_buy": 50.0,
            "step_sell": 150.0,
            "alpha": 0.8,
            "asymmetry_ratio": 0.33,
            "mode": "trending",
            "reason": "Bull flag detected",
        })
        
        # Call it N times to trigger hysteresis
        result = {}
        for _ in range(3):
            result = check_and_adapt(engine, bars, hysteresis_bars=3)
            
        print(f"Adaptation Result: {result}")
        
        # Verify fields were updated on the ENGINE, not just state
        self.assertTrue(result.get("changed"))
        self.assertEqual(engine.base_step_buy_px, 50.0)
        self.assertEqual(engine.base_step_sell_px, 150.0)
        self.assertEqual(engine.close_alpha, 0.8)

    def test_reports_previous_structure_on_flip(self):
        """Verify shadow/event payload keeps the pre-flip structure identity."""
        engine = MagicMock()
        engine.symbol = "TEST_ETH"
        engine.base_step_buy_px = 100.0
        engine.base_step_sell_px = 100.0
        engine.close_alpha = 0.5
        engine.state = MagicMock()

        bars = [{"close": 10.0}] * 60

        import structure_shapeshifter_bridge

        structure_shapeshifter_bridge.detect_structure = MagicMock(
            side_effect=[
                {"primary_structure": "range"},
                {"primary_structure": "range"},
                {"primary_structure": "trend"},
                {
                    "primary_structure": "trend",
                    "lattice_geometry": {
                        "step_buy": 50.0,
                        "step_sell": 150.0,
                        "alpha": 0.8,
                        "asymmetry_ratio": 0.33,
                        "mode": "trending",
                        "reason": "Trend confirmed",
                    },
                },
            ]
        )
        structure_shapeshifter_bridge.structure_to_geometry = MagicMock(
            side_effect=[
                {
                    "step_buy": 90.0,
                    "step_sell": 110.0,
                    "alpha": 0.55,
                    "asymmetry_ratio": 0.82,
                    "mode": "range",
                    "reason": "Range baseline",
                },
                {
                    "step_buy": 50.0,
                    "step_sell": 150.0,
                    "alpha": 0.8,
                    "asymmetry_ratio": 0.33,
                    "mode": "trending",
                    "reason": "Trend confirmed",
                },
            ]
        )

        for _ in range(2):
            result = check_and_adapt(engine, bars, hysteresis_bars=2)
        self.assertTrue(result.get("changed"))
        self.assertEqual(result.get("from_structure"), "unknown")
        self.assertEqual(result.get("to_structure"), "range")

        for _ in range(2):
            result = check_and_adapt(engine, bars, hysteresis_bars=2)
        self.assertTrue(result.get("changed"))
        self.assertEqual(result.get("from_structure"), "range")
        self.assertEqual(result.get("to_structure"), "trend")

if __name__ == "__main__":
    unittest.main()
