#!/usr/bin/env python3
"""Tests for the cross-family profit mode classifier."""
import unittest
from scripts.profit_mode_classifier import (
    classify_profit_mode,
    score_shape_for_mode,
    MICRO_HARVEST_ROUND_TRIP_THRESHOLD,
    TOXIC_BURST_THRESHOLD,
)


class TestProfitModeClassifier(unittest.TestCase):

    def test_micro_harvest_classification(self):
        """Quiet tape with high mean-reversion, manageable spread -> micro_harvest."""
        result = classify_profit_mode(
            same_bar_round_trip_rate=0.35,  # above threshold
            spread_to_step_ratio=0.20,  # below max
            directional_bias=0.05,
            regime="mixed",
        )
        self.assertEqual(result.profit_mode, "micro_harvest")
        self.assertGreater(result.confidence, 0.5)
        self.assertTrue(result.tape_signals.get("micro_harvest_trigger"))

    def test_micro_harvest_blocked_by_spread(self):
        """Round-trip rate says micro exists but spread too high -> friction."""
        result = classify_profit_mode(
            same_bar_round_trip_rate=0.40,  # high round-trip
            spread_to_step_ratio=0.50,  # above micro max
            directional_bias=0.05,
        )
        self.assertNotEqual(result.profit_mode, "micro_harvest")
        self.assertTrue(result.tape_signals.get("micro_harvest_blocked_by_spread"))
        self.assertGreater(result.mode_scores["friction_survivor"], 0)

    def test_guarded_toxic_flow_from_burst(self):
        """Burst count above threshold -> guarded_toxic_flow."""
        result = classify_profit_mode(
            same_bar_open_burst_count=5,  # above threshold of 3
            directional_bias=0.10,
        )
        self.assertEqual(result.profit_mode, "guarded_toxic_flow")
        self.assertGreater(result.confidence, 0.7)
        self.assertTrue(result.tape_signals.get("toxic_burst_trigger"))

    def test_guarded_toxic_flow_from_verdict(self):
        """Never-green verdict -> guarded_toxic_flow with high confidence."""
        result = classify_profit_mode(
            first_path_verdict="never_green_toxic_continuation",
        )
        self.assertEqual(result.profit_mode, "guarded_toxic_flow")
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_friction_survivor_from_range_ratio(self):
        """Spread dominates range -> friction_survivor."""
        result = classify_profit_mode(
            spread_to_range_ratio=0.75,  # above 0.6 threshold
            spread_to_step_ratio=0.40,  # above 0.35 threshold
            directional_bias=0.05,
        )
        self.assertEqual(result.profit_mode, "friction_survivor")
        self.assertGreater(result.confidence, 0.5)
        self.assertTrue(result.tape_signals.get("friction_trigger_range"))

    def test_trend_harvest_classification(self):
        """Strong directional bias in trending regime -> trend_harvest."""
        result = classify_profit_mode(
            directional_bias=0.25,  # above 0.15 threshold
            regime="trending",
            same_bar_round_trip_rate=0.10,  # below max round-trip
        )
        self.assertEqual(result.profit_mode, "trend_harvest")
        self.assertGreater(result.confidence, 0.5)
        self.assertTrue(result.tape_signals.get("trend_trigger"))

    def test_cash_repair_from_close_conversion(self):
        """Close conversion pressure -> cash_repair_harvest."""
        result = classify_profit_mode(
            close_conversion_pressure=True,
            negative_carry_pressure=False,
        )
        self.assertEqual(result.profit_mode, "cash_repair_harvest")
        self.assertGreaterEqual(result.confidence, 0.6)
        self.assertTrue(result.tape_signals.get("cash_repair_trigger"))

    def test_cash_repair_from_negative_carry(self):
        """Negative carry pressure -> cash_repair_harvest."""
        result = classify_profit_mode(
            close_conversion_pressure=False,
            negative_carry_pressure=True,
        )
        self.assertEqual(result.profit_mode, "cash_repair_harvest")
        self.assertGreater(result.confidence, 0.5)

    def test_cash_repair_both_pressure(self):
        """Both pressures -> highest cash_repair confidence."""
        result = classify_profit_mode(
            close_conversion_pressure=True,
            negative_carry_pressure=True,
        )
        self.assertEqual(result.profit_mode, "cash_repair_harvest")
        self.assertGreaterEqual(result.confidence, 0.8)

    def test_balanced_harvest_default(self):
        """No dominant signal -> balanced_harvest baseline."""
        result = classify_profit_mode(
            directional_bias=0.08,  # below trend threshold
            same_bar_round_trip_rate=0.10,  # below micro threshold
            spread_to_step_ratio=0.15,  # below friction threshold
            regime="mixed",
        )
        self.assertEqual(result.profit_mode, "balanced_harvest")

    def test_no_motion_suppresses_all(self):
        """ATR <= 0 suppresses all modes."""
        result = classify_profit_mode(
            current_atr=0.0,
            same_bar_round_trip_rate=0.50,
            directional_bias=0.30,
        )
        self.assertEqual(result.profit_mode, "balanced_harvest")
        self.assertLessEqual(result.confidence, 0.1)
        self.assertTrue(result.tape_signals.get("no_motion"))

    def test_mode_scores_all_present(self):
        """All mode scores should be in the output."""
        result = classify_profit_mode()
        expected_modes = {
            "micro_harvest", "trend_harvest", "cash_repair_harvest",
            "friction_survivor", "guarded_toxic_flow", "balanced_harvest",
        }
        self.assertEqual(set(result.mode_scores.keys()), expected_modes)

    def test_burst_count_uses_max_of_tick_or_bar(self):
        """Burst count should use max of same_bar and same_tick."""
        result_bar = classify_profit_mode(same_bar_open_burst_count=5, same_tick_open_burst_count=1)
        result_tick = classify_profit_mode(same_bar_open_burst_count=1, same_tick_open_burst_count=5)
        result_both = classify_profit_mode(same_bar_open_burst_count=5, same_tick_open_burst_count=5)
        # All should trigger toxic flow
        self.assertEqual(result_bar.profit_mode, "guarded_toxic_flow")
        self.assertEqual(result_tick.profit_mode, "guarded_toxic_flow")
        self.assertEqual(result_both.profit_mode, "guarded_toxic_flow")

    def test_confidence_scales_with_signal_strength(self):
        """Stronger signals should produce higher confidence."""
        weak_trend = classify_profit_mode(directional_bias=0.16, regime="trending")
        strong_trend = classify_profit_mode(directional_bias=0.40, regime="trending")
        self.assertGreater(strong_trend.confidence, weak_trend.confidence)

    def test_negative_directional_bias_treated_as_absolute(self):
        """Negative bias should be treated as absolute value for trend detection."""
        result = classify_profit_mode(
            directional_bias=-0.25,
            regime="trending",
        )
        self.assertEqual(result.profit_mode, "trend_harvest")

    def test_friction_beats_trend_when_scores_tie(self):
        """Cost domination should beat pure trend participation on equal confidence."""
        result = classify_profit_mode(
            directional_bias=0.40,
            regime="trending",
            spread_to_step_ratio=1.2,
            spread_to_range_ratio=0.8,
        )
        self.assertEqual(result.mode_scores["trend_harvest"], 1.0)
        self.assertEqual(result.mode_scores["friction_survivor"], 1.0)
        self.assertEqual(result.profit_mode, "friction_survivor")

    def test_toxic_beats_friction_when_scores_tie(self):
        """Toxic burst evidence should beat friction when both are maxed."""
        result = classify_profit_mode(
            same_tick_open_burst_count=10,
            spread_to_step_ratio=1.2,
            spread_to_range_ratio=0.8,
            directional_bias=0.40,
            regime="trending",
        )
        self.assertEqual(result.mode_scores["guarded_toxic_flow"], 1.0)
        self.assertEqual(result.mode_scores["friction_survivor"], 1.0)
        self.assertEqual(result.profit_mode, "guarded_toxic_flow")


class TestShapeModeScoring(unittest.TestCase):

    def test_shape_matches_micro_mode(self):
        """Shape with cash_harvest profile should score well for micro_harvest."""
        shape = {
            "monetization_profile": "cash_harvest",
            "close": {"alpha": 0.4},
            "risk_profile": "conservative",
        }
        score = score_shape_for_mode(shape, "micro_harvest", 0.7)
        self.assertGreater(score, 0)

    def test_shape_mismatches_micro_mode(self):
        """Shape with trend_extension should lose points for micro_harvest."""
        shape = {
            "monetization_profile": "trend_extension",
        }
        score = score_shape_for_mode(shape, "micro_harvest", 0.7)
        self.assertLess(score, 0)

    def test_shape_matches_trend_mode(self):
        """Shape with trend_harvest profile should score well for trend_harvest."""
        shape = {
            "monetization_profile": "trend_harvest",
            "risk_profile": "aggressive",
        }
        score = score_shape_for_mode(shape, "trend_harvest", 0.8)
        self.assertGreater(score, 0)

    def test_guarded_toxic_all_shapes_eligible(self):
        """Guarded-toxic mode still keeps defensive cash-harvest shapes eligible."""
        shape = {"monetization_profile": "cash_harvest"}
        score = score_shape_for_mode(shape, "guarded_toxic_flow", 0.9)
        self.assertGreater(score, 0)

    def test_guarded_toxic_prefers_defensive_shape_over_trend_extension(self):
        defensive = {
            "monetization_profile": "cash_harvest",
            "risk_profile": "conservative",
            "portfolio_profile": "medium",
            "close": {"alpha": 0.5},
        }
        aggressive = {
            "monetization_profile": "trend_extension",
            "risk_profile": "balanced",
            "portfolio_profile": "heavy",
            "close": {"alpha": 1.0},
        }
        defensive_score = score_shape_for_mode(defensive, "guarded_toxic_flow", 1.0)
        aggressive_score = score_shape_for_mode(aggressive, "guarded_toxic_flow", 1.0)
        self.assertGreater(defensive_score, aggressive_score)

    def test_fast_close_bonus_for_cash_repair(self):
        """Low alpha should get bonus for cash_repair mode."""
        shape_fast = {"monetization_profile": "cash_harvest", "close": {"alpha": 0.3}}
        shape_slow = {"monetization_profile": "cash_harvest", "close": {"alpha": 0.9}}
        score_fast = score_shape_for_mode(shape_fast, "cash_repair_harvest", 0.7)
        score_slow = score_shape_for_mode(shape_slow, "cash_repair_harvest", 0.7)
        self.assertGreater(score_fast, score_slow)

    def test_conservative_bonus_for_friction(self):
        """Conservative risk profile should get bonus for friction_survivor."""
        shape_conservative = {
            "monetization_profile": "friction_survivor",
            "risk_profile": "conservative",
        }
        shape_aggressive = {
            "monetization_profile": "friction_survivor",
            "risk_profile": "aggressive",
        }
        score_con = score_shape_for_mode(shape_conservative, "friction_survivor", 0.8)
        score_agg = score_shape_for_mode(shape_aggressive, "friction_survivor", 0.8)
        self.assertGreater(score_con, score_agg)


if __name__ == "__main__":
    unittest.main()
