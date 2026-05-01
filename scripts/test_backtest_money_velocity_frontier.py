#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_money_velocity_frontier as frontier


class MoneyVelocityFrontierTests(unittest.TestCase):
    def test_resolve_symbols_uses_shape_regime_intersection_when_requested(self) -> None:
        original_shape = frontier.load_shape_symbols
        original_regime = frontier.load_regime_symbols
        try:
            frontier.load_shape_symbols = lambda path: ["GBPUSD", "EURUSD", "USDJPY", "ETHUSD"]  # type: ignore[assignment]
            frontier.load_regime_symbols = lambda path: ["EURUSD", "GBPUSD", "BTCUSD", "ETHUSD"]  # type: ignore[assignment]
            args = type(
                "Args",
                (),
                {
                    "all_shape_symbols": True,
                    "symbols": ["BTCUSD"],
                    "shape_library": "shape.json",
                    "regime_json": "regime.json",
                },
            )()
            symbols = frontier.resolve_symbols(args)
        finally:
            frontier.load_shape_symbols = original_shape  # type: ignore[assignment]
            frontier.load_regime_symbols = original_regime  # type: ignore[assignment]
        self.assertEqual(symbols, ["GBPUSD", "EURUSD", "ETHUSD"])

    def test_select_candidate_close_specs_keeps_top_profiles_and_forced_experiments(self) -> None:
        original_load_shape = frontier.load_shape
        original_normalize = frontier.normalize_close_specs
        try:
            frontier.load_shape = lambda symbol: {"close": {"style": "all_profitable", "alpha": 0.5, "sell_gap": 1, "buy_gap": 3}}  # type: ignore[assignment]
            frontier.normalize_close_specs = lambda shape: [  # type: ignore[assignment]
                frontier.CloseSpec(style="all_profitable", alpha=0.5, sell_gap=1, buy_gap=3, label="shape_contract"),
                frontier.CloseSpec(style="all_profitable", alpha=1.0, sell_gap=1, buy_gap=1, label="sweep_fast_shallow"),
                frontier.CloseSpec(style="all_profitable", alpha=1.0, sell_gap=0, buy_gap=0, label="sweep_fast_gap0"),
                frontier.CloseSpec(style="inner", alpha=1.0, sell_gap=1, buy_gap=2, label="inner_fast_shallow"),
                frontier.CloseSpec(style="harvest_inner_hold_frontier", alpha=1.0, sell_gap=1, buy_gap=2, label="harvest_inner_hold_frontier"),
                frontier.CloseSpec(style="close_early", alpha=1.0, sell_gap=1, buy_gap=1, label="close_early"),
                frontier.CloseSpec(style="close_early_funded_rescue", alpha=1.0, sell_gap=1, buy_gap=1, label="close_early_funded_rescue"),
                frontier.CloseSpec(style="close_deep", alpha=1.0, sell_gap=1, buy_gap=1, label="close_deep_shallow"),
                frontier.CloseSpec(style="hybrid_early_hold_deep", alpha=1.0, sell_gap=1, buy_gap=1, label="hybrid_early_hold_deep"),
                frontier.CloseSpec(style="hybrid_early_hold_deep_funded_rescue", alpha=1.0, sell_gap=1, buy_gap=1, label="hybrid_early_hold_deep_funded_rescue"),
                frontier.CloseSpec(style="range_sweep_trend_reclaim_funded_rescue", alpha=1.0, sell_gap=1, buy_gap=1, label="range_sweep_trend_reclaim_funded_rescue"),
                frontier.CloseSpec(style="outer_funded_rescue", alpha=1.0, sell_gap=1, buy_gap=1, label="outer_fast_shallow_funded_rescue"),
                frontier.CloseSpec(style="all_profitable_funded_rescue", alpha=1.0, sell_gap=0, buy_gap=0, label="sweep_fast_gap0_funded_rescue"),
                frontier.CloseSpec(style="book_flat_sweep", alpha=1.0, sell_gap=0, buy_gap=0, label="book_flat_gap0"),
            ]
            specs = frontier.select_candidate_close_specs(
                symbol="GBPUSD",
                study_rows=[
                    {"symbol": "GBPUSD", "close_profile": "sweep_fast_shallow", "gross_positive_booked_usd_per_hour": 2.8, "realized_usd_per_hour": 2.8, "unified_objective_score": 20, "combined_net_usd": 10, "avg_close_usd": 1},
                    {"symbol": "GBPUSD", "close_profile": "inner_fast_shallow", "gross_positive_booked_usd_per_hour": 2.6, "realized_usd_per_hour": 2.6, "unified_objective_score": 19, "combined_net_usd": 9, "avg_close_usd": 1},
                ],
                top_n_profiles=2,
            )
        finally:
            frontier.load_shape = original_load_shape  # type: ignore[assignment]
            frontier.normalize_close_specs = original_normalize  # type: ignore[assignment]
        labels = [spec.label for spec in specs]
        self.assertEqual(labels[:2], ["sweep_fast_shallow", "inner_fast_shallow"])
        self.assertIn("harvest_inner_hold_frontier", labels)
        self.assertIn("sweep_fast_gap0", labels)
        self.assertIn("book_flat_gap0", labels)
        self.assertIn("close_early", labels)
        self.assertIn("close_early_funded_rescue", labels)
        self.assertIn("close_deep_shallow", labels)
        self.assertIn("hybrid_early_hold_deep", labels)
        self.assertIn("hybrid_early_hold_deep_funded_rescue", labels)
        self.assertIn("range_sweep_trend_reclaim_funded_rescue", labels)
        self.assertIn("outer_fast_shallow_funded_rescue", labels)
        self.assertIn("sweep_fast_gap0_funded_rescue", labels)
        self.assertIn("shape_contract", labels)

    def test_build_local_frontier_contracts_uses_finer_geometry_grid(self) -> None:
        original_resolve = frontier.resolve_base_contract
        original_select = frontier.select_candidate_close_specs
        try:
            frontier.resolve_base_contract = lambda symbol, timeframe: frontier.DeploymentContract(  # type: ignore[assignment]
                symbol=symbol,
                timeframe=timeframe,
                shape_id="shape",
                step_buy_px=1.0,
                step_sell_px=2.0,
                max_open_per_side=12,
                close_style="all_profitable",
                close_alpha=0.5,
                sell_gap=1,
                buy_gap=1,
                rearm_variant="rearm_lvl2_exc1",
                rearm_cooldown_bars=0,
                momentum_gate=False,
                variant_label="base",
                step_scale=1.0,
                cap_delta=0,
                close_profile="shape_contract",
            )
            frontier.select_candidate_close_specs = lambda **kwargs: [  # type: ignore[assignment]
                frontier.CloseSpec(style="all_profitable", alpha=1.0, sell_gap=1, buy_gap=1, label="sweep_fast_shallow"),
                frontier.CloseSpec(style="close_early", alpha=1.0, sell_gap=1, buy_gap=1, label="close_early"),
            ]
            contracts = frontier.build_local_frontier_contracts(
                symbol="EURUSD",
                timeframe="M15",
                study_rows=[],
                top_profiles=2,
                step_scales=[0.6, 0.9, 1.0],
                cap_deltas=[0, 3, 6],
            )
        finally:
            frontier.resolve_base_contract = original_resolve  # type: ignore[assignment]
            frontier.select_candidate_close_specs = original_select  # type: ignore[assignment]
        self.assertEqual(len(contracts), 18)
        labels = {contract["variant_label"] for contract in contracts}
        self.assertIn("sweep_fast_shallow_frontier_step0.60_cap+0", labels)
        self.assertIn("close_early_frontier_step0.90_cap+3", labels)
        self.assertIn("sweep_fast_shallow_frontier_step1.00_cap+6", labels)

    def test_filter_frontier_rows_applies_survivability_constraints(self) -> None:
        args = type(
            "Args",
            (),
            {
                "max_mae_abs_usd": 30000.0,
                "max_final_open": 30,
                "max_max_open": 50,
                "require_realized_cover": True,
                "starting_balance_usd": 100.0,
                "hard_floor_usd": 50.0,
            },
        )()
        rows = [
            {
                "variant_label": "unsafe",
                "max_adverse_excursion_usd": -45600.0,
                "final_open_count": 48,
                "max_open_total": 71,
                "min_realized_cover_gap_usd": -12.0,
                "min_combined_equity_delta_usd": -72.0,
            },
            {
                "variant_label": "safer",
                "max_adverse_excursion_usd": -29000.0,
                "final_open_count": 19,
                "max_open_total": 47,
                "min_realized_cover_gap_usd": 4.0,
                "min_combined_equity_delta_usd": -18.0,
            },
            {
                "variant_label": "bankrupts_floor",
                "max_adverse_excursion_usd": -12000.0,
                "final_open_count": 8,
                "max_open_total": 18,
                "min_realized_cover_gap_usd": 1.0,
                "min_combined_equity_delta_usd": -60.0,
            },
        ]
        kept = frontier.filter_frontier_rows(rows, args)
        self.assertEqual([row["variant_label"] for row in kept], ["safer"])


if __name__ == "__main__":
    unittest.main()
