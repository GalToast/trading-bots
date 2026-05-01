#!/usr/bin/env python3
"""Unit tests for anticipatory rearm token generation."""
import sys
sys.path.insert(0, 'scripts')

import unittest
from tick_penetration_lattice_core import TickRearmToken


class MockVariant:
    """Mock rearm variant for testing anticipatory logic."""
    name = "test"
    min_level_idx = 2
    excursion_levels = 1
    anticipatory_tokens = 10
    anticipatory_steps_above = 1
    anticipatory_step_size = 50.0


class TestAnticipatoryRearm(unittest.TestCase):
    def test_anticipatory_token_field(self):
        """Test that TickRearmToken has anticipatory field with default False."""
        token = TickRearmToken(direction="SELL", level=75000, level_idx=20)
        self.assertFalse(token.anticipatory)
        token.anticipatory = True
        self.assertTrue(token.anticipatory)

    def test_anticipatory_token_serialization(self):
        """Test that anticipatory token serializes correctly via asdict."""
        from dataclasses import asdict
        token = TickRearmToken(
            direction="SELL",
            level=75000.0,
            level_idx=20,
            armed=False,
            cooldown_until_time=0,
            anticipatory=True,
        )
        d = asdict(token)
        self.assertTrue(d["anticipatory"])
        self.assertEqual(d["direction"], "SELL")
        self.assertEqual(d["level"], 75000.0)

    def test_token_from_dict_with_anticipatory(self):
        """Test that TickRearmToken can be constructed from dict with anticipatory field."""
        d = {
            "direction": "SELL",
            "level": 75000.0,
            "level_idx": 20,
            "armed": False,
            "cooldown_until_time": 0,
            "anticipatory": True,
        }
        token = TickRearmToken(**d)
        self.assertTrue(token.anticipatory)
        self.assertEqual(token.direction, "SELL")
        self.assertEqual(token.level, 75000.0)


class TestAnticipatoryGenerationLogic(unittest.TestCase):
    """Test the anticipatory generation logic in isolation."""

    def test_generate_tokens_during_short_squeeze(self):
        """Test token generation when SELL tokens are exhausted."""
        variant = MockVariant()

        # Simulate: no SELL tokens, has SELL positions
        sell_tokens = []  # No SELL tokens (exhausted)
        sell_positions = [{"fill_price": 74050.0}]  # Has SELL positions

        # Condition: SELL tokens exhausted AND SELL positions exist
        should_generate = len(sell_tokens) == 0 and len(sell_positions) > 0
        self.assertTrue(should_generate)

        # Generate tokens
        highest_existing = 74050.0  # From existing SELL position
        step = variant.anticipatory_step_size
        n = variant.anticipatory_tokens
        start_level = highest_existing + (variant.anticipatory_steps_above * step)

        new_tokens = []
        for i in range(n):
            entry_price = start_level + (i * step)
            new_tokens.append({
                "direction": "SELL",
                "level": entry_price,
                "anticipatory": True,
            })

        self.assertEqual(len(new_tokens), 10)
        self.assertEqual(new_tokens[0]["level"], 74100.0)  # 74050 + 50
        self.assertEqual(new_tokens[1]["level"], 74150.0)  # 74050 + 100

    def test_no_generation_when_tokens_exist(self):
        """Test that tokens are NOT generated when regular tokens exist."""
        sell_tokens = [{"direction": "SELL", "level": 74100.0}]  # Has SELL token
        sell_positions = [{"fill_price": 74050.0}]

        should_generate = len(sell_tokens) == 0 and len(sell_positions) > 0
        self.assertFalse(should_generate)  # Should NOT generate

    def test_cancel_on_mean_reversion(self):
        """Test that anticipatory tokens are cancelled on mean reversion."""
        sell_positions = [{"fill_price": 74050.0}]
        highest_sell = max(float(t["fill_price"]) for t in sell_positions)

        # Mid price below highest SELL (mean reversion)
        mid = 74000.0
        should_cancel = mid < highest_sell
        self.assertTrue(should_cancel)

        # Mid price above highest SELL (no reversion)
        mid = 74100.0
        should_cancel = mid < highest_sell
        self.assertFalse(should_cancel)


if __name__ == "__main__":
    unittest.main()
