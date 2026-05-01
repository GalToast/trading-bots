#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_adaptive_deployment_study as study


class AdaptiveDeploymentStudyTests(unittest.TestCase):
    def test_resolve_shape_step_contract_supports_symmetric_atr_multiple(self) -> None:
        shape = {
            "step_method": {
                "kind": "atr_multiple",
                "coeff": 1.0,
            }
        }
        regime_row = {"current_atr": 0.167}
        step_buy_px, step_sell_px = study.resolve_shape_step_contract("USDJPY", shape, regime_row)
        self.assertEqual(step_buy_px, 0.167)
        self.assertEqual(step_sell_px, 0.167)

    def test_resolve_shape_step_contract_gbpusd_uses_atr_coefficients(self) -> None:
        shape = {
            "step_method": {
                "kind": "atr_multiple_asymmetric",
                "buy_coeff": 1.0,
                "sell_coeff": 0.5,
            }
        }
        regime_row = {"current_atr": 0.0011}
        step_buy_px, step_sell_px = study.resolve_shape_step_contract("GBPUSD", shape, regime_row)
        self.assertEqual(step_buy_px, 0.0011)
        self.assertEqual(step_sell_px, 0.00055)

    def test_resolve_shape_step_contract_btc_uses_range_atr_formula_step(self) -> None:
        shape = {"step_method": {"kind": "range_atr_formula"}}
        regime_row = {"range_atr_formula_step": 465.09136}
        step_buy_px, step_sell_px = study.resolve_shape_step_contract("BTCUSD", shape, regime_row)
        self.assertEqual(step_buy_px, 465.09136)
        self.assertEqual(step_sell_px, 465.09136)

    def test_normalize_close_specs_includes_base_and_cash_harvest(self) -> None:
        shape = {
            "close": {
                "style": "all_profitable",
                "alpha": 0.5,
                "sell_gap": 1,
                "buy_gap": 3,
            }
        }
        specs = study.normalize_close_specs(shape)
        labels = [spec.label for spec in specs]
        self.assertIn("shape_contract", labels)
        self.assertIn("cash_harvest", labels)
        self.assertIn("outer_guarded", labels)
        self.assertIn("inner_guarded", labels)
        self.assertIn("sweep_guarded", labels)
        self.assertIn("outer_deep", labels)
        self.assertIn("sweep_fast", labels)
        self.assertIn("sweep_fast_shallow", labels)
        self.assertIn("book_flat_sweep", labels)
        self.assertIn("sweep_fast_gap0", labels)
        self.assertIn("book_flat_gap0", labels)
        self.assertIn("outer_fast", labels)
        self.assertIn("outer_fast_shallow", labels)
        self.assertIn("inner_fast", labels)
        self.assertIn("inner_fast_shallow", labels)
        self.assertIn("harvest_inner_hold_frontier", labels)
        self.assertIn("harvest_inner_hold_two_frontiers", labels)
        self.assertIn("harvest_inner_funded_rescue", labels)
        self.assertIn("harvest_inner_hold_two_frontiers_funded_rescue", labels)
        self.assertIn("ema_ladder_sweep", labels)
        self.assertIn("ema_ladder_inner", labels)
        self.assertIn("fib_reclaim_sweep", labels)
        self.assertIn("ema_span_fib_sweep", labels)
        self.assertIn("ema_span_fib_inner", labels)
        self.assertIn("ema_midspan_fib_sweep", labels)
        self.assertIn("ema_midspan_fib_inner", labels)
        self.assertIn("ema_midspan_fib_shallow_sweep", labels)
        self.assertIn("triple_anchor_span_sweep", labels)
        self.assertIn("triple_anchor_span_inner", labels)
        self.assertIn("triple_anchor_fast_span_sweep", labels)
        self.assertIn("triple_anchor_fast_span_inner", labels)
        self.assertIn("stack_depth_scaled_gap", labels)
        self.assertIn("range_sweep_trend_reclaim", labels)
        self.assertIn("close_early", labels)
        self.assertIn("close_early_funded_rescue", labels)
        self.assertIn("close_early_shallow", labels)
        self.assertIn("close_deep_shallow", labels)
        self.assertIn("hybrid_early_hold_deep", labels)
        self.assertIn("hybrid_early_hold_deep_funded_rescue", labels)
        self.assertIn("range_sweep_trend_reclaim_funded_rescue", labels)
        self.assertIn("outer_fast_shallow_funded_rescue", labels)
        self.assertIn("sweep_fast_gap0_funded_rescue", labels)
        self.assertEqual(len(specs), 42)
        by_label = {spec.label: spec for spec in specs}
        self.assertEqual(by_label["inner_fast_shallow"].style, "inner")
        self.assertEqual(by_label["inner_fast_shallow"].alpha, 1.0)
        self.assertEqual(by_label["inner_fast_shallow"].sell_gap, 1)
        self.assertEqual(by_label["inner_fast_shallow"].buy_gap, 2)
        self.assertEqual(by_label["harvest_inner_hold_frontier"].style, "harvest_inner_hold_frontier")
        self.assertEqual(by_label["harvest_inner_hold_two_frontiers"].style, "harvest_inner_hold_two_frontiers")
        self.assertEqual(by_label["harvest_inner_funded_rescue"].style, "harvest_inner_funded_rescue")
        self.assertEqual(
            by_label["harvest_inner_hold_two_frontiers_funded_rescue"].style,
            "harvest_inner_hold_two_frontiers_funded_rescue",
        )
        self.assertEqual(by_label["ema_ladder_sweep"].style, "ema_ladder_sweep")
        self.assertEqual(by_label["ema_ladder_inner"].style, "ema_ladder_inner")
        self.assertEqual(by_label["fib_reclaim_sweep"].style, "fib_reclaim_sweep")
        self.assertEqual(by_label["ema_span_fib_sweep"].style, "ema_span_fib_sweep")
        self.assertEqual(by_label["ema_span_fib_inner"].style, "ema_span_fib_inner")
        self.assertEqual(by_label["ema_midspan_fib_sweep"].style, "ema_midspan_fib_sweep")
        self.assertEqual(by_label["ema_midspan_fib_inner"].style, "ema_midspan_fib_inner")
        self.assertEqual(by_label["ema_midspan_fib_shallow_sweep"].style, "ema_midspan_fib_shallow_sweep")
        self.assertEqual(by_label["triple_anchor_span_sweep"].style, "triple_anchor_span_sweep")
        self.assertEqual(by_label["triple_anchor_span_inner"].style, "triple_anchor_span_inner")
        self.assertEqual(by_label["triple_anchor_fast_span_sweep"].style, "triple_anchor_fast_span_sweep")
        self.assertEqual(by_label["triple_anchor_fast_span_inner"].style, "triple_anchor_fast_span_inner")
        self.assertEqual(by_label["stack_depth_scaled_gap"].style, "stack_depth_scaled_gap")
        self.assertEqual(by_label["range_sweep_trend_reclaim"].style, "range_sweep_trend_reclaim")

    def test_build_contract_variants_creates_cross_product(self) -> None:
        base = study.DeploymentContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="gbpusd_trend_harvest_v1",
            step_buy_px=0.0011,
            step_sell_px=0.00055,
            max_open_per_side=12,
            close_style="all_profitable",
            close_alpha=0.5,
            sell_gap=1,
            buy_gap=3,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            variant_label="base",
            step_scale=1.0,
            cap_delta=0,
            close_profile="shape_contract",
        )
        original_loader = study.load_shape
        try:
            study.load_shape = lambda symbol: {  # type: ignore[assignment]
                "close": {
                    "style": "all_profitable",
                    "alpha": 0.5,
                    "sell_gap": 1,
                    "buy_gap": 3,
                }
            }
            variants = study.build_contract_variants(base)
        finally:
            study.load_shape = original_loader  # type: ignore[assignment]
        self.assertEqual(len(variants), 378)
        labels = {variant.variant_label for variant in variants}
        self.assertIn("shape_contract_step1.00_cap0", labels)
        self.assertIn("cash_harvest_step0.75_cap-3", labels)
        self.assertIn("outer_guarded_step1.25_cap+3", labels)
        self.assertIn("inner_guarded_step1.00_cap0", labels)
        self.assertIn("sweep_guarded_step1.00_cap0", labels)
        self.assertIn("outer_deep_step1.25_cap+3", labels)
        self.assertIn("sweep_fast_step1.00_cap0", labels)
        self.assertIn("sweep_fast_shallow_step1.00_cap0", labels)
        self.assertIn("book_flat_sweep_step1.00_cap0", labels)
        self.assertIn("sweep_fast_gap0_step1.00_cap0", labels)
        self.assertIn("book_flat_gap0_step1.00_cap0", labels)
        self.assertIn("outer_fast_step1.25_cap+3", labels)
        self.assertIn("outer_fast_shallow_step1.25_cap+3", labels)
        self.assertIn("inner_fast_step1.00_cap0", labels)
        self.assertIn("inner_fast_shallow_step1.00_cap0", labels)
        self.assertIn("harvest_inner_hold_frontier_step1.00_cap0", labels)
        self.assertIn("harvest_inner_hold_two_frontiers_step1.00_cap0", labels)
        self.assertIn("harvest_inner_funded_rescue_step1.00_cap0", labels)
        self.assertIn("harvest_inner_hold_two_frontiers_funded_rescue_step1.00_cap0", labels)
        self.assertIn("ema_ladder_sweep_step1.00_cap0", labels)
        self.assertIn("ema_ladder_inner_step1.00_cap0", labels)
        self.assertIn("fib_reclaim_sweep_step1.00_cap0", labels)
        self.assertIn("ema_span_fib_sweep_step1.00_cap0", labels)
        self.assertIn("ema_span_fib_inner_step1.00_cap0", labels)
        self.assertIn("ema_midspan_fib_sweep_step1.00_cap0", labels)
        self.assertIn("ema_midspan_fib_inner_step1.00_cap0", labels)
        self.assertIn("ema_midspan_fib_shallow_sweep_step1.00_cap0", labels)
        self.assertIn("triple_anchor_span_sweep_step1.00_cap0", labels)
        self.assertIn("triple_anchor_span_inner_step1.00_cap0", labels)
        self.assertIn("triple_anchor_fast_span_sweep_step1.00_cap0", labels)
        self.assertIn("triple_anchor_fast_span_inner_step1.00_cap0", labels)
        self.assertIn("stack_depth_scaled_gap_step1.00_cap0", labels)
        self.assertIn("range_sweep_trend_reclaim_step1.00_cap0", labels)
        self.assertIn("close_early_step1.00_cap0", labels)
        self.assertIn("close_early_funded_rescue_step1.00_cap0", labels)
        self.assertIn("close_early_shallow_step1.00_cap0", labels)
        self.assertIn("close_deep_shallow_step1.00_cap0", labels)
        self.assertIn("hybrid_early_hold_deep_step1.00_cap0", labels)
        self.assertIn("hybrid_early_hold_deep_funded_rescue_step1.00_cap0", labels)
        self.assertIn("range_sweep_trend_reclaim_funded_rescue_step1.00_cap0", labels)
        self.assertIn("outer_fast_shallow_funded_rescue_step1.00_cap0", labels)
        self.assertIn("sweep_fast_gap0_funded_rescue_step1.00_cap0", labels)

    def test_build_contract_variants_can_filter_close_labels(self) -> None:
        base = study.DeploymentContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="gbpusd_trend_harvest_v1",
            step_buy_px=0.0011,
            step_sell_px=0.00055,
            max_open_per_side=12,
            close_style="all_profitable",
            close_alpha=0.5,
            sell_gap=1,
            buy_gap=3,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            variant_label="base",
            step_scale=1.0,
            cap_delta=0,
            close_profile="shape_contract",
        )
        original_loader = study.load_shape
        try:
            study.load_shape = lambda symbol: {  # type: ignore[assignment]
                "close": {
                    "style": "all_profitable",
                    "alpha": 0.5,
                    "sell_gap": 1,
                    "buy_gap": 3,
                }
            }
            variants = study.build_contract_variants(
                base,
                include_close_labels={"ema_ladder_sweep", "triple_anchor_span_inner"},
            )
        finally:
            study.load_shape = original_loader  # type: ignore[assignment]
        self.assertEqual(len(variants), 18)
        labels = {variant.close_profile for variant in variants}
        self.assertEqual(labels, {"ema_ladder_sweep", "triple_anchor_span_inner"})

    def test_resolve_close_positions_supports_hybrid_styles(self) -> None:
        profitable = [0, 1, 2, 3]
        self.assertEqual(
            study._resolve_close_positions(
                "book_flat_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=4,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "harvest_inner_hold_frontier",
                profitable_positions=profitable,
                gap=1,
                stack_depth=4,
            ),
            [1, 2, 3],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "harvest_inner_hold_two_frontiers",
                profitable_positions=profitable,
                gap=1,
                stack_depth=4,
            ),
            [2, 3],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "harvest_inner",
                profitable_positions=profitable,
                gap=1,
                stack_depth=4,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "stack_depth_scaled_gap",
                profitable_positions=profitable,
                gap=1,
                stack_depth=7,
            ),
            [1, 2, 3],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "stack_depth_scaled_gap",
                profitable_positions=profitable,
                gap=1,
                stack_depth=9,
            ),
            [2, 3],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "range_sweep_trend_reclaim",
                profitable_positions=profitable,
                gap=1,
                stack_depth=3,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "range_sweep_trend_reclaim",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            [0],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_ladder_inner",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            [0],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_ladder_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_span_fib_inner",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            [0],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_span_fib_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_midspan_fib_inner",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            [0],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_midspan_fib_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "ema_midspan_fib_shallow_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "triple_anchor_span_inner",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            [0],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "triple_anchor_span_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            profitable,
        )
        self.assertEqual(
            study._resolve_close_positions(
                "triple_anchor_fast_span_inner",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            [0],
        )
        self.assertEqual(
            study._resolve_close_positions(
                "triple_anchor_fast_span_sweep",
                profitable_positions=profitable,
                gap=1,
                stack_depth=5,
            ),
            profitable,
        )

    def test_close_positions_book_flat_sweep_waits_for_non_negative_book(self) -> None:
        contract = study.DeploymentContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="gbpusd_trend_harvest_v1",
            step_buy_px=1.0,
            step_sell_px=1.0,
            max_open_per_side=12,
            close_style="book_flat_sweep",
            close_alpha=1.0,
            sell_gap=1,
            buy_gap=1,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            variant_label="book_flat_sweep",
            step_scale=1.0,
            cap_delta=0,
            close_profile="book_flat_sweep",
        )
        variant = type("Variant", (), {"min_level_idx": 1})()
        base_stats = {
            "realized_net_usd": 0.0,
            "realized_closes": 0,
            "wins": 0,
            "losses": 0,
            "gross_positive_booked_usd": 0.0,
            "rescue_spend_usd": 0.0,
            "rescue_closes": 0,
            "close_pnls": [],
            "bar_time": 0,
        }
        original_unit_pnl = study.unit_pnl_usd
        try:
            def fake_unit_pnl(symbol, direction, entry_price, exit_price, spread_px):
                if direction == "SELL" and float(entry_price) == 102.0:
                    return 2.0
                if direction == "SELL" and float(entry_price) == 101.0:
                    return 1.0
                if direction == "BUY" and float(entry_price) == 110.0:
                    return -10.0
                if direction == "BUY" and float(entry_price) == 100.5:
                    return -1.0
                return 0.0

            study.unit_pnl_usd = fake_unit_pnl  # type: ignore[assignment]
            losing_book = [
                study.Ticket(direction="SELL", entry_price=102.0, opened_time=0),
                study.Ticket(direction="SELL", entry_price=101.0, opened_time=0),
                study.Ticket(direction="BUY", entry_price=110.0, opened_time=0),
            ]
            still_open = study._close_positions(
                symbol="GBPUSD",
                direction="SELL",
                tickets=losing_book,
                trigger_price=100.0,
                bar_extreme=100.0,
                contract=contract,
                spread_px=0.0,
                anchor=100.0,
                step_px=1.0,
                variant=variant,
                tokens=[],
                stats=dict(base_stats),
            )
            self.assertEqual(len(still_open), 3)
            flat_book = [
                study.Ticket(direction="SELL", entry_price=102.0, opened_time=0),
                study.Ticket(direction="SELL", entry_price=101.0, opened_time=0),
                study.Ticket(direction="BUY", entry_price=100.5, opened_time=0),
            ]
            closed = study._close_positions(
                symbol="GBPUSD",
                direction="SELL",
                tickets=flat_book,
                trigger_price=100.0,
                bar_extreme=100.0,
                contract=contract,
                spread_px=0.0,
                anchor=100.0,
                step_px=1.0,
                variant=variant,
                tokens=[],
                stats=dict(base_stats),
            )
            self.assertEqual(len(closed), 1)
        finally:
            study.unit_pnl_usd = original_unit_pnl  # type: ignore[assignment]

    def test_select_funded_rescue_ticket_prefers_old_extreme_loss_within_budget(self) -> None:
        oldest_extreme = study.Ticket(direction="BUY", entry_price=1.0900, opened_time=1)
        newer_inner = study.Ticket(direction="BUY", entry_price=1.0980, opened_time=2700)
        original_unit_pnl = study.unit_pnl_usd
        try:
            study.unit_pnl_usd = lambda *args, **kwargs: -10.0 if float(args[2]) < 1.095 else -4.0  # type: ignore[assignment]
            chosen = study._select_funded_rescue_ticket(
                symbol="EURUSD",
                direction="BUY",
                ordered=[newer_inner, oldest_extreme],
                trigger_price=1.0850,
                spread_px=0.0001,
                anchor=1.1000,
                step_px=0.0010,
                current_bar_time=7200,
                timeframe_name="M15",
                rescue_budget=1000.0,
            )
        finally:
            study.unit_pnl_usd = original_unit_pnl  # type: ignore[assignment]
        self.assertIs(chosen, oldest_extreme)

    def test_simulate_contract_tracks_cover_and_equity_floor_metrics(self) -> None:
        contract = study.DeploymentContract(
            symbol="GBPUSD",
            timeframe="M15",
            shape_id="gbpusd_trend_harvest_v1",
            step_buy_px=0.001,
            step_sell_px=0.001,
            max_open_per_side=1,
            close_style="all_profitable",
            close_alpha=1.0,
            sell_gap=1,
            buy_gap=1,
            rearm_variant="rearm_lvl2_exc1",
            rearm_cooldown_bars=0,
            momentum_gate=False,
            variant_label="probe",
            step_scale=1.0,
            cap_delta=0,
            close_profile="shape_contract",
        )
        bars = [
            {"time": 0, "open": 1.0000, "high": 1.0000, "low": 1.0000, "close": 1.0000, "tick_volume": 1},
            {"time": 900, "open": 1.0000, "high": 1.0012, "low": 1.0000, "close": 1.0010, "tick_volume": 1},
            {"time": 1800, "open": 1.0010, "high": 1.0500, "low": 1.0010, "close": 1.0500, "tick_volume": 1},
        ]
        symbol_info = type(
            "SymbolInfo",
            (),
            {"spread": 0.0, "point": 0.0001, "currency_profit": "USD", "trade_contract_size": 100000.0},
        )()
        result = study.simulate_contract(contract, bars, symbol_info=symbol_info)
        self.assertIn("min_realized_cover_gap_usd", result)
        self.assertIn("min_combined_equity_delta_usd", result)
        self.assertIn("realized_cover_violation_bars", result)
        self.assertLess(result["min_realized_cover_gap_usd"], 0.0)
        self.assertLess(result["min_combined_equity_delta_usd"], 0.0)
        self.assertGreater(result["realized_cover_violation_bars"], 0)


if __name__ == "__main__":
    unittest.main()
