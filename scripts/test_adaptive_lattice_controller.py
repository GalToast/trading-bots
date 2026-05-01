#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import adaptive_lattice_controller as controller
import build_adaptive_lattice_proof_board as board


class AdaptiveLatticeControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = controller.load_json(controller.DEFAULT_LIBRARY_PATH)

    def test_gbpusd_trending_prefers_trend_harvest_shape(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "GBPUSD",
            controller.ControlContext(
                regime="trending",
                directional_bias=0.25,  # above trend threshold
                same_bar_round_trip_rate=0.10,  # below micro threshold
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recommended_shape_id"], "gbpusd_trend_harvest_v1")
        self.assertEqual(result["profit_mode"], "trend_harvest")

    def test_usdjpy_is_blocked_when_bounded_fault_is_active(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "USDJPY",
            controller.ControlContext(regime="mixed"),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("usdjpy_bounded_survival_v1", result["alternatives"])

    def test_low_motion_alone_keeps_microstructure_candidate_alive(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "GBPUSD",
            controller.ControlContext(
                regime="trending",
                atr_percentile=5.0,
                directional_bias=0.04,
                avg_range=0.001,
                current_atr=0.001,
                same_bar_round_trip_rate=0.30,  # above micro threshold
                spread_to_step_ratio=0.15,  # below micro max spread
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recommended_shape_id"], "gbpusd_micro_harvest_v1")
        self.assertEqual(result["extractability_state"], "active_microstructure_candidate")
        self.assertEqual(result["profit_mode"], "micro_harvest")
        self.assertIn("micro fluctuations", result["extractability_read"])

    def test_low_motion_plus_friction_blocks_shape_selection(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "GBPUSD",
            controller.ControlContext(
                regime="trending",
                atr_percentile=5.0,
                directional_bias=0.04,
                avg_range=0.001,
                current_atr=0.001,
                spread_to_range_ratio=0.7,
                high_friction=True,
            ),
        )
        self.assertEqual(result["status"], "unextractable_cost_dominated")
        self.assertEqual(result["recommended_shape_id"], "")
        self.assertEqual(result["extractability_state"], "unextractable_cost_dominated")
        self.assertEqual(result["profit_mode"], "parked_no_edge")

    def test_toxic_flow_is_hard_block_not_advisory(self) -> None:
        """Toxic first-path should BLOCK shape recommendation (survival constraint)."""
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                avg_range=300.0,
                current_atr=280.0,
                first_path_verdict="never_green_toxic_continuation",
            ),
        )
        self.assertEqual(result["status"], "blocked_by_survival_constraint")
        self.assertIn("toxic_first_path", result["survival_block_reason"])
        self.assertIn("guard_open_admission", result["runtime_overlays"])

    def test_burst_concentration_adds_cluster_escape_overlay(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                avg_range=300.0,
                current_atr=280.0,
                # No toxic first-path — test burst overlay behavior independently
                same_bar_open_burst_count=4,
                same_tick_open_burst_count=4,
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("cluster_aware_escape", result["runtime_overlays"])
        self.assertIn("suppress_additional_levels_after_burst", result["runtime_overlays"])
        self.assertIn("same-bar/tick burst count", result["runtime_overlay_read"])

    def test_btc_monetization_pressure_prefers_cash_harvest_shape(self) -> None:
        context = controller.context_from_regime_row(
            {
                "symbol": "BTCUSD",
                "regime": "TRANSITION",
                "avg_range": 300.0,
                "current_atr": 280.0,
                "range_atr_ratio": 1.0,
            },
            runner_session_trade_closes=0,
            runner_session_trade_realized_usd=0.0,
            pre_start_state_carry_realized_usd=-17.77,
        )
        result = controller.recommend_shape(self.library, "BTCUSD", context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recommended_shape_id"], "btcusd_rangeatr_cash_harvest_v1")
        self.assertTrue(result["close_conversion_pressure"])
        self.assertTrue(result["negative_carry_pressure"])
        self.assertEqual(result["profit_mode"], "cash_repair_harvest")

    def test_btc_without_monetization_pressure_keeps_extension_shape(self) -> None:
        context = controller.context_from_regime_row(
            {
                "symbol": "BTCUSD",
                "regime": "STRONG_TREND",  # trending regime for trend_harvest mode
                "avg_range": 300.0,
                "current_atr": 280.0,
                "range_atr_ratio": 1.0,
                "directional_bias": 0.20,  # above trend threshold
            },
            runner_session_trade_closes=2,
            runner_session_trade_realized_usd=45.0,
            pre_start_state_carry_realized_usd=10.0,
        )
        result = controller.recommend_shape(self.library, "BTCUSD", context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recommended_shape_id"], "btcusd_regime_rangeatr_v1")
        self.assertFalse(result["close_conversion_pressure"])
        self.assertFalse(result["negative_carry_pressure"])
        self.assertEqual(result["profit_mode"], "trend_harvest")

    def test_friction_tie_beats_trend_in_controller_profit_mode(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                directional_bias=0.40,
                spread_to_step_ratio=1.2,
                spread_to_range_ratio=0.8,
                current_atr=280.0,
                avg_range=300.0,
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["profit_mode"], "friction_survivor")

    def test_guarded_toxic_profit_mode_requests_guard_open_admission(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                directional_bias=0.40,
                spread_to_step_ratio=1.2,
                spread_to_range_ratio=0.8,
                same_tick_open_burst_count=10,
                current_atr=280.0,
                avg_range=300.0,
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["profit_mode"], "guarded_toxic_flow")
        self.assertIn("guard_open_admission", result["runtime_overlays"])

    def test_guarded_toxic_flow_prefers_cash_harvest_shape_over_trend_extension(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                directional_bias=0.40,
                spread_to_step_ratio=1.2,
                spread_to_range_ratio=0.8,
                same_tick_open_burst_count=10,
                current_atr=280.0,
                avg_range=300.0,
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["profit_mode"], "guarded_toxic_flow")
        self.assertEqual(result["recommended_shape_id"], "btcusd_rangeatr_cash_harvest_v1")

    def test_context_from_regime_row_ingests_realized_performance_fields(self) -> None:
        context = controller.context_from_regime_row(
            {
                "symbol": "BTCUSD",
                "regime": "TRANSITION",
                "avg_range": 300.0,
                "current_atr": 280.0,
                "range_atr_ratio": 1.0,
                "realized_closes": 8,
                "realized_net_usd": 24.0,
                "anchor_resets": 2,
            },
            runner_session_trade_closes=1,
            runner_session_trade_realized_usd=5.0,
        )

        self.assertEqual(context.realized_close_count, 8)
        self.assertEqual(context.realized_net_usd, 24.0)
        self.assertEqual(context.realized_avg_per_close, 3.0)
        self.assertEqual(context.anchor_reset_count, 2)

    def test_runtime_context_can_override_library_performance_summary(self) -> None:
        result = controller.recommend_shape(
            self.library,
            "GBPUSD",
            controller.ControlContext(
                regime="trending",
                realized_close_count=25,
                realized_net_usd=25.0,
                realized_avg_per_close=1.0,
                anchor_reset_count=1,
            ),
        )

        self.assertEqual(result["recommended_shape_id"], "gbpusd_trend_harvest_v1")
        self.assertEqual(result["performance_summary"], "$1.00/close over 25 closes; resets=1")

    def test_proof_board_rows_include_expected_symbols(self) -> None:
        blocker_state = {
            "blocker_id": "bounded_close_style_runtime_fault",
            "active": True,
            "watchdog_statuses": ["quarantined"],
            "read": "bounded close-style runtime fault active",
        }
        original_packet_path = board.PACKET_PATH
        board.PACKET_PATH = Path(__file__).resolve().parent / "_missing_packet_board.json"
        try:
            rows = board.build_rows(
                self.library,
                blocker_state,
                {
                    "GBPUSD": {"symbol": "GBPUSD", "regime": "WEAK_TREND", "atr_percentile": 30.0, "directional_bias": 0.5},
                    "BTCUSD": {"symbol": "BTCUSD", "regime": "TRANSITION", "atr_percentile": 45.0, "directional_bias": 0.2},
                },
            )
        finally:
            board.PACKET_PATH = original_packet_path
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertIn("GBPUSD", by_symbol)
        self.assertIn("BTCUSD", by_symbol)
        self.assertEqual(by_symbol["GBPUSD"]["recommended_shape_id"], "gbpusd_trend_harvest_v1")
        self.assertEqual(by_symbol["GBPUSD"]["profit_mode"], "trend_harvest")
        self.assertEqual(by_symbol["USDJPY"]["status"], "blocked")


    def test_small_sample_positive_ev_scores_above_zero(self) -> None:
        """1-2 closes with positive EV should get a small but nonzero score boost."""
        # Baseline: no EV evidence
        baseline = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending"),
        )
        # 1 close at +$50
        one_close = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending", realized_close_count=1, realized_net_usd=50.0, realized_avg_per_close=50.0),
        )
        # 2 closes at +$50 each (net $100)
        two_close = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending", realized_close_count=2, realized_net_usd=100.0, realized_avg_per_close=50.0),
        )
        self.assertGreater(one_close, baseline, "1 close at +$50 should score above baseline")
        self.assertGreater(two_close, one_close, "2 closes at +$50/c should score above 1 close")

    def test_small_sample_negative_ev_scores_below_zero(self) -> None:
        """1-2 closes with negative EV should get a small penalty."""
        baseline = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending"),
        )
        one_close_neg = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending", realized_close_count=1, realized_net_usd=-50.0, realized_avg_per_close=-50.0),
        )
        self.assertLess(one_close_neg, baseline, "1 close at -$50 should score below baseline")

    def test_mature_sample_ev_dominates_small_sample(self) -> None:
        """3+ closes with positive EV should get full scoring, not small-sample discounting."""
        three_close = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending", realized_close_count=3, realized_net_usd=30.0, realized_avg_per_close=10.0),
        )
        # 3 closes at $10/c should score more than 2 closes at $50/c (despite lower $/c)
        # because the sample maturity gate unlocks full scoring
        two_close = controller.score_shape(
            {"regime_targets": ["trending"], "risk_profile": "balanced", "monetization_profile": "trend_harvest",
             "portfolio_profile": "medium", "close": {"style": "all_profitable", "alpha": 0.5},
             "evidence": {"status": "shadow_ready"}},
            controller.ControlContext(regime="trending", realized_close_count=2, realized_net_usd=100.0, realized_avg_per_close=50.0),
        )
        self.assertGreater(three_close, two_close, "3 closes at $10/c should score above 2 closes at $50/c")

    def test_unified_objective_replaces_ad_hoc_ev_not_stacks(self) -> None:
        """When context has realized evidence, unified objective fires and ad-hoc EV is skipped.

        Before this fix: ad-hoc close_efficiency + unified close_efficiency both added to score.
        After fix: only unified objective covers close_efficiency, win_rate, reset_penalty
        when ctx_close_count > 0. Ad-hoc EV only fires for evidence-dict-only data.
        """
        shape = {
            "regime_targets": ["trending"],
            "risk_profile": "balanced",
            "monetization_profile": "trend_harvest",
            "portfolio_profile": "medium",
            "close": {"style": "all_profitable", "alpha": 0.5},
            "evidence": {"status": "survivor"},
        }
        # Context with realized evidence -> unified objective fires
        ctx = controller.ControlContext(
            regime="trending",
            realized_close_count=15,
            realized_net_usd=120.0,
            realized_avg_per_close=8.0,
            realized_win_rate=0.73,
            anchor_reset_count=2,
            open_count=3,
        )
        score = controller.score_shape(shape, ctx)

        # Compute what the unified objective contributes independently
        import unified_objective as uo
        unified = uo.UnifiedObjective.evaluate(uo.ObjectiveInput(
            realized_net_usd=120.0,
            close_count=15,
            floating_usd=0.0,
            open_count=3,
            anchor_reset_count=2,
            max_adverse_excursion_usd=0.0,
            first_path_verdict="",
            realized_win_rate=0.73,
        ))

        # The base score (regime + risk profile + evidence status) is ~8.
        # If dedup is correct, score should be roughly base + unified, NOT base + unified + ad_hoc.
        # Ad-hoc EV for $8/c at 15 closes would be ~6.5 (min(8,10)*sqrt(15/25)=6.2).
        # If double-counted: score would be ~8 + 6.0 + 6.2 = ~20.2
        # If deduped: score would be ~8 + 6.0 = ~14
        base_score = 5.0 + 1.0 + 2.0  # regime + risk_profile + survivor
        max_reasonable = base_score + unified.total + 3.0  # +3 for mode matching tolerance
        self.assertLess(score, max_reasonable,
                        f"Score {score} exceeds base({base_score}) + unified({unified.total}) + tolerance, "
                        "suggesting ad-hoc EV is double-counting with unified objective")

    def test_survival_constraint_blocks_toxic_first_path(self) -> None:
        """Even a high-scoring shape should be blocked when first-path is toxic."""
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                avg_range=300.0,
                current_atr=280.0,
                first_path_verdict="never_green_toxic_continuation",
                realized_close_count=1,
                realized_net_usd=-17.77,
                realized_avg_per_close=-17.77,
                anchor_reset_count=10,
            ),
        )
        self.assertEqual(result["status"], "blocked_by_survival_constraint")
        self.assertIn("toxic_first_path", result["survival_block_reason"])

    def test_survival_constraint_blocks_catastrophic_reset_rate(self) -> None:
        """Shape should be blocked when resets exceed closes (reset rate > 1.0)."""
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                avg_range=300.0,
                current_atr=280.0,
                realized_close_count=5,
                realized_net_usd=25.0,
                realized_avg_per_close=5.0,
                anchor_reset_count=8,  # 8 resets vs 5 closes = 1.6 reset rate
            ),
        )
        self.assertEqual(result["status"], "blocked_by_survival_constraint")
        self.assertIn("catastrophic_reset_rate", result["survival_block_reason"])

    def test_survival_constraint_allows_healthy_shape(self) -> None:
        """Healthy shape (low reset rate, non-toxic) should NOT be blocked."""
        result = controller.recommend_shape(
            self.library,
            "BTCUSD",
            controller.ControlContext(
                regime="trending",
                avg_range=300.0,
                current_atr=280.0,
                realized_close_count=10,
                realized_net_usd=100.0,
                realized_avg_per_close=10.0,
                anchor_reset_count=1,  # 0.1 reset rate — healthy
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("survival_block_reason", result)


if __name__ == "__main__":
    unittest.main()
