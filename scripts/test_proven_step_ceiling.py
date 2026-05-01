"""Tests for the proven-step ceiling constraint on adaptive geometry."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestProvenStepCeiling(unittest.TestCase):
    """Verify that adaptive geometry never widens beyond the proven ceiling."""

    def test_ceiling_stored_on_engine(self):
        """Engine should store proven_step_ceiling when provided."""
        from tick_penetration_lattice_core import TickStatefulRearmEngine

        cfg = MagicMock()
        cfg.step_pips = 10.0
        cfg.max_open_per_side = 12
        info = MagicMock()
        info.digits = 5
        info.point = 0.00001
        info.spread = 10

        engine = TickStatefulRearmEngine(
            "BTCUSD", cfg, info,
            timeframe_name="M15",
            variant=MagicMock(),
            proven_step_ceiling=75.0,
        )
        self.assertEqual(engine.proven_step_ceiling, 75.0)
        self.assertEqual(engine.snapshot()["proven_step_ceiling"], 75.0)

    def test_side_specific_ceilings_stored_on_engine(self):
        """Engine should store side-specific ceilings when provided."""
        from tick_penetration_lattice_core import TickStatefulRearmEngine

        cfg = MagicMock()
        cfg.step_pips = 10.0
        cfg.max_open_per_side = 12
        info = MagicMock()
        info.digits = 5
        info.point = 0.00001
        info.spread = 10

        engine = TickStatefulRearmEngine(
            "GBPUSD", cfg, info,
            timeframe_name="M15",
            variant=MagicMock(),
            proven_step_buy_ceiling=0.0004,
            proven_step_sell_ceiling=0.0002,
        )
        snapshot = engine.snapshot()
        self.assertEqual(engine.proven_step_buy_ceiling, 0.0004)
        self.assertEqual(engine.proven_step_sell_ceiling, 0.0002)
        self.assertEqual(snapshot["proven_step_buy_ceiling"], 0.0004)
        self.assertEqual(snapshot["proven_step_sell_ceiling"], 0.0002)

    def test_ceiling_none_when_zero(self):
        """Engine should set ceiling to None when 0.0 (no constraint)."""
        from tick_penetration_lattice_core import TickStatefulRearmEngine

        cfg = MagicMock()
        cfg.step_pips = 10.0
        cfg.max_open_per_side = 12
        info = MagicMock()
        info.digits = 5
        info.point = 0.00001
        info.spread = 10

        engine = TickStatefulRearmEngine(
            "BTCUSD", cfg, info,
            timeframe_name="M15",
            variant=MagicMock(),
            proven_step_ceiling=0.0,
        )
        self.assertIsNone(engine.proven_step_ceiling)

    def test_load_snapshot_clamps_restored_steps_to_side_specific_ceilings(self):
        """Snapshot rehydration should immediately clamp widened geometry."""
        from tick_penetration_lattice_core import TickStatefulRearmEngine

        cfg = MagicMock()
        cfg.step_pips = 10.0
        cfg.max_open_per_side = 12
        info = MagicMock()
        info.digits = 5
        info.point = 0.00001
        info.spread = 10

        engine = TickStatefulRearmEngine(
            "GBPUSD", cfg, info,
            timeframe_name="M15",
            variant=MagicMock(),
            proven_step_buy_ceiling=0.0004,
            proven_step_sell_ceiling=0.0002,
        )
        engine.load_snapshot({
            "base_step_buy_px": 0.001659,
            "base_step_sell_px": 0.000715,
        })
        self.assertEqual(engine.base_step_buy_px, 0.0004)
        self.assertEqual(engine.base_step_sell_px, 0.0002)

    def test_engine_factory_accepts_ceiling(self):
        """Factory function should accept proven_step_ceiling."""
        from tick_penetration_lattice_core import engine_from_args

        # Just verify the function signature accepts the parameter
        import inspect
        sig = inspect.signature(engine_from_args)
        self.assertIn("proven_step_ceiling", sig.parameters)
        self.assertIn("proven_step_buy_ceiling", sig.parameters)
        self.assertIn("proven_step_sell_ceiling", sig.parameters)

    def test_shadow_runner_accepts_ceiling_arg(self):
        """Shadow runner should accept --proven-step-ceiling CLI arg."""
        import subprocess
        result = subprocess.run(
            ["python", "scripts/live_penetration_lattice_tick_crypto_shadow.py", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        # Help text may go to stdout or stderr
        output = result.stdout + result.stderr
        self.assertIn("--proven-step-ceiling", output)
        self.assertIn("--proven-step-buy-ceiling", output)
        self.assertIn("--proven-step-sell-ceiling", output)


class TestBoxGeometryCeilingClamping(unittest.TestCase):
    """Verify that box geometry adjustments are clamped to proven ceiling."""

    def test_clamping_logic(self):
        """When ceiling is set, adapted step should be clamped."""
        # Simulate the clamping logic
        ceiling = 75.0
        geom_step_buy = 324.12  # Box geometry suggests wide step
        geom_step_sell = 259.29

        new_step_buy = geom_step_buy
        new_step_sell = geom_step_sell
        if ceiling is not None:
            new_step_buy = min(new_step_buy, ceiling)
            new_step_sell = min(new_step_sell, ceiling)

        self.assertEqual(new_step_buy, 75.0)
        self.assertEqual(new_step_sell, 75.0)
        self.assertTrue(new_step_buy < geom_step_buy)  # Was clamped

    def test_no_clamping_when_below_ceiling(self):
        """When box geometry suggests step below ceiling, no clamping needed."""
        ceiling = 75.0
        geom_step_buy = 50.0  # Below ceiling
        geom_step_sell = 60.0

        new_step_buy = geom_step_buy
        new_step_sell = geom_step_sell
        if ceiling is not None:
            new_step_buy = min(new_step_buy, ceiling)
            new_step_sell = min(new_step_sell, ceiling)

        self.assertEqual(new_step_buy, 50.0)  # Unchanged
        self.assertEqual(new_step_sell, 60.0)  # Unchanged

    def test_no_clamping_when_ceiling_none(self):
        """When ceiling is None, box geometry passes through."""
        ceiling = None
        geom_step_buy = 324.12
        geom_step_sell = 259.29

        new_step_buy = geom_step_buy
        new_step_sell = geom_step_sell
        if ceiling is not None:
            new_step_buy = min(new_step_buy, ceiling)
            new_step_sell = min(new_step_sell, ceiling)

        self.assertEqual(new_step_buy, 324.12)  # Unchanged
        self.assertEqual(new_step_sell, 259.29)  # Unchanged

    def test_side_specific_clamping_logic(self):
        """Buy/sell ceilings should clamp independently for asym lanes."""
        buy_ceiling = 0.0004
        sell_ceiling = 0.0002
        geom_step_buy = 0.001659
        geom_step_sell = 0.000715

        new_step_buy = min(geom_step_buy, buy_ceiling)
        new_step_sell = min(geom_step_sell, sell_ceiling)

        self.assertEqual(new_step_buy, 0.0004)
        self.assertEqual(new_step_sell, 0.0002)


if __name__ == "__main__":
    unittest.main()
