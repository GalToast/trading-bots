#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_snake_counter_web as study


class SnakeCounterWebTests(unittest.TestCase):
    def test_apply_closes_can_hold_frontier(self) -> None:
        tickets = [
            study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1),
            study.SnakeTicket(direction="SELL", entry_price=1.1010, opened_time=2),
            study.SnakeTicket(direction="SELL", entry_price=1.1000, opened_time=3),
        ]
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            retrace_steps=1,
            hold_frontier=1,
            rebase_on_flat=False,
            max_open_per_side=8,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="none",
            hedge_trigger_depth=0,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        stats = {
            "realized_net_usd": 0.0,
            "gross_positive_booked_usd": 0.0,
            "realized_closes": 0,
            "wins": 0,
            "close_pnls": [],
            "float_zero_closes": 0,
            "profit_lock_closes": 0,
            "min_realized_cover_gap_usd": 0.0,
            "min_combined_equity_delta_usd": 0.0,
            "realized_cover_violation_bars": 0,
        }
        original_unit_pnl = study.unit_pnl_usd
        try:
            study.unit_pnl_usd = lambda symbol, direction, entry_price, close_price, spread_px: max(  # type: ignore[assignment]
                0.0,
                (entry_price - close_price) * 100000.0,
            )
            study._apply_closes(
                symbol="GBPUSD",
                symbol_info=None,
                tickets=tickets,
                price=1.0990,
                spread_px=0.0,
                contract=contract,
                stats=stats,
            )
        finally:
            study.unit_pnl_usd = original_unit_pnl  # type: ignore[assignment]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].entry_price, 1.1020)
        self.assertEqual(stats["realized_closes"], 2)

    def test_apply_closes_float_zero_harvests_profitable_book_when_portfolio_flat(self) -> None:
        tickets = [
            study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1),
            study.SnakeTicket(direction="SELL", entry_price=1.1010, opened_time=2),
            study.SnakeTicket(direction="BUY", entry_price=1.0970, opened_time=3),
        ]
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            retrace_steps=10,
            hold_frontier=0,
            rebase_on_flat=False,
            max_open_per_side=8,
            controller_mode="static",
            portfolio_close_mode="float_zero",
            hedge_mode="none",
            hedge_trigger_depth=0,
            hedge_profit_threshold_steps=0,
            variant_label="float_zero_test",
        )
        stats = {
            "realized_net_usd": 0.0,
            "gross_positive_booked_usd": 0.0,
            "realized_closes": 0,
            "wins": 0,
            "close_pnls": [],
            "float_zero_closes": 0,
            "profit_lock_closes": 0,
            "min_realized_cover_gap_usd": 0.0,
            "min_combined_equity_delta_usd": 0.0,
            "realized_cover_violation_bars": 0,
        }
        original_research_unit_pnl = study.research_unit_pnl_usd
        try:
            pnl_map = {
                ("SELL", 1.1020): 4.0,
                ("SELL", 1.1010): 3.0,
                ("BUY", 1.0970): -6.0,
            }
            study.research_unit_pnl_usd = lambda symbol, direction, entry_price, close_price, spread_px, symbol_info: pnl_map[(direction, round(entry_price, 4))]  # type: ignore[assignment]
            study._apply_closes(
                symbol="GBPUSD",
                symbol_info=None,
                tickets=tickets,
                price=1.1016,
                spread_px=0.0,
                contract=contract,
                stats=stats,
            )
        finally:
            study.research_unit_pnl_usd = original_research_unit_pnl  # type: ignore[assignment]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].direction, "BUY")
        self.assertEqual(stats["realized_closes"], 2)
        self.assertEqual(stats["float_zero_closes"], 2)

    def test_update_floating_stats_tracks_min_and_max(self) -> None:
        tickets = [
            study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1),
            study.SnakeTicket(direction="BUY", entry_price=1.0970, opened_time=2),
        ]
        stats = {
            "realized_net_usd": 5.0,
            "min_floating_pnl_usd": 0.0,
            "max_floating_pnl_usd": 0.0,
            "min_combined_equity_usd": 0.0,
            "max_combined_equity_usd": 0.0,
            "min_realized_cover_gap_usd": 0.0,
            "min_combined_equity_delta_usd": 0.0,
            "realized_cover_violation_bars": 0,
            "max_used_margin_usd": 0.0,
            "min_free_margin_usd": 50.0,
            "min_margin_level_pct": float("inf"),
            "margin_stopout_bars": 0,
        }
        original_research_unit_pnl = study.research_unit_pnl_usd
        try:
            pnl_map = {
                ("SELL", 1.1020): -7.0,
                ("BUY", 1.0970): 2.5,
            }
            study.research_unit_pnl_usd = lambda symbol, direction, entry_price, close_price, spread_px, symbol_info: pnl_map[(direction, round(entry_price, 4))]  # type: ignore[assignment]
            study._update_floating_stats(
                symbol="GBPUSD",
                symbol_info=None,
                tickets=tickets,
                price=1.1000,
                spread_px=0.0,
                stats=stats,
                starting_balance_usd=50.0,
                account_leverage=500.0,
                margin_stopout_level_pct=50.0,
            )
        finally:
            study.research_unit_pnl_usd = original_research_unit_pnl  # type: ignore[assignment]
        self.assertEqual(stats["min_floating_pnl_usd"], -4.5)
        self.assertEqual(stats["max_floating_pnl_usd"], 0.0)
        self.assertEqual(stats["min_combined_equity_usd"], 0.0)
        self.assertEqual(stats["max_combined_equity_usd"], 0.5)
        self.assertEqual(stats["min_realized_cover_gap_usd"], 0.0)
        self.assertEqual(stats["min_combined_equity_delta_usd"], 0.0)
        self.assertEqual(stats["realized_cover_violation_bars"], 0)
        self.assertGreater(stats["max_used_margin_usd"], 0.0)
        self.assertGreaterEqual(stats["min_free_margin_usd"], 0.0)
        self.assertGreater(stats["min_margin_level_pct"], 50.0)
        self.assertEqual(stats["margin_stopout_bars"], 0)

    def test_cross_level_helpers_identify_new_levels(self) -> None:
        self.assertEqual(
            study._cross_up_levels(anchor=1.1000, start=1.1000, end=1.10035, step_px=0.0001, last_level=0),
            [1, 2, 3],
        )
        self.assertEqual(
            study._cross_down_levels(anchor=1.1000, start=1.1000, end=1.09965, step_px=0.0001, last_level=0),
            [1, 2, 3],
        )

    def test_segment_path_prefers_close_direction(self) -> None:
        up_bar = {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1005}
        down_bar = {"open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.0995}
        self.assertEqual(study._segment_path(up_bar), [1.1, 1.101, 1.099, 1.1005])
        self.assertEqual(study._segment_path(down_bar), [1.1, 1.099, 1.101, 1.0995])

    def test_resolve_controller_state_tightens_in_compression(self) -> None:
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0003,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=16,
            controller_mode="ema_ribbon",
            portfolio_close_mode="none",
            hedge_mode="none",
            hedge_trigger_depth=0,
            hedge_profit_threshold_steps=0,
            variant_label="ctrl",
        )
        step_px, sell_divisor, buy_divisor, rebase_allowed = study._resolve_controller_state(
            contract,
            {
                "ema_fast_3": 1.1000,
                "ema_light_12": 1.1001,
                "ema_mid_64": 1.10015,
                "ema_mid_128": 1.1002,
            },
            pip_px=0.0001,
        )
        self.assertLess(step_px, contract.step_px)
        self.assertEqual(sell_divisor, 1)
        self.assertEqual(buy_divisor, 1)
        self.assertTrue(rebase_allowed)

    def test_build_contracts_crosses_controller_and_cap_values(self) -> None:
        original_symbol_info = study.mt5.symbol_info
        try:
            study.mt5.symbol_info = lambda symbol: SimpleNamespace(point=0.00001, digits=5)  # type: ignore[assignment]
            args = SimpleNamespace(
                symbols=["GBPUSD"],
                timeframe="M1",
                step_pips=[1.0],
                retrace_steps=[2],
                hold_frontier=[0],
                controller_modes=["static", "ema_ribbon_hyper"],
                portfolio_close_modes=["none", "float_zero"],
                hedge_modes=["none", "same_level", "profit_lock"],
                hedge_trigger_depths=[4],
                hedge_profit_threshold_steps=[2],
                max_open_per_side_values=[16, 32],
            )
            contracts = study.build_contracts(args)
        finally:
            study.mt5.symbol_info = original_symbol_info  # type: ignore[assignment]
        self.assertEqual(len(contracts), 48)
        labels = {contract.variant_label for contract in contracts}
        self.assertIn("snake_step1pip_retrace2_hold0_static_none_hedgenone_cap16_rebase", labels)
        self.assertIn("snake_step1pip_retrace2_hold0_ema_ribbon_hyper_float_zero_hedgesame_level_cap32_fixed", labels)
        self.assertIn("snake_step1pip_retrace2_hold0_static_none_hedgeprofit_lock2_cap16_rebase", labels)

    def test_same_level_hedge_adds_opposite_ticket(self) -> None:
        tickets = [
            study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1),
        ]
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=False,
            max_open_per_side=8,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="same_level",
            hedge_trigger_depth=0,
            hedge_profit_threshold_steps=0,
            variant_label="hedge_test",
        )
        stats = {"opens": 1, "hedge_opens": 0}
        study._maybe_add_hedge_ticket(
            tickets=tickets,
            contract=contract,
            level_direction="SELL",
            entry_price=1.1020,
            bar_time=1,
            stats=stats,
        )
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[-1].direction, "BUY")
        self.assertEqual(tickets[-1].ticket_kind, "hedge")
        self.assertEqual(stats["hedge_opens"], 1)

    def test_depth_threshold_hedge_waits_for_core_depth(self) -> None:
        tickets = [
            study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1),
            study.SnakeTicket(direction="SELL", entry_price=1.1030, opened_time=2),
        ]
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=False,
            max_open_per_side=8,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="depth_threshold",
            hedge_trigger_depth=3,
            hedge_profit_threshold_steps=0,
            variant_label="depth_hedge_test",
        )
        stats = {"opens": 2, "hedge_opens": 0}
        study._maybe_add_hedge_ticket(
            tickets=tickets,
            contract=contract,
            level_direction="SELL",
            entry_price=1.1040,
            bar_time=3,
            stats=stats,
        )
        self.assertEqual(len(tickets), 2)
        tickets.append(study.SnakeTicket(direction="SELL", entry_price=1.1040, opened_time=3))
        study._maybe_add_hedge_ticket(
            tickets=tickets,
            contract=contract,
            level_direction="SELL",
            entry_price=1.1050,
            bar_time=4,
            stats=stats,
        )
        self.assertEqual(len(tickets), 4)
        self.assertEqual(tickets[-1].direction, "BUY")
        self.assertEqual(stats["hedge_opens"], 1)

    def test_profit_lock_closes_core_with_extra_hedge_tax(self) -> None:
        tickets = [
            study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1),
            study.SnakeTicket(direction="BUY", entry_price=1.0990, opened_time=2, ticket_kind="hedge"),
        ]
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=False,
            max_open_per_side=8,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="profit_lock",
            hedge_trigger_depth=0,
            hedge_profit_threshold_steps=2,
            variant_label="profit_lock_test",
        )
        stats = {
            "realized_net_usd": 0.0,
            "gross_positive_booked_usd": 0.0,
            "realized_closes": 0,
            "wins": 0,
            "close_pnls": [],
            "opens": 2,
            "hedge_opens": 0,
            "float_zero_closes": 0,
            "profit_lock_closes": 0,
            "min_realized_cover_gap_usd": 0.0,
            "min_combined_equity_delta_usd": 0.0,
            "realized_cover_violation_bars": 0,
        }
        original_research_unit_pnl = study.research_unit_pnl_usd
        try:
            pnl_map = {
                ("SELL", 1.1020): 6.0,
                ("BUY", 1.1000): -1.0,
                ("BUY", 1.0990): 0.5,
            }
            study.research_unit_pnl_usd = lambda symbol, direction, entry_price, close_price, spread_px, symbol_info: pnl_map[(direction, round(entry_price, 4))]  # type: ignore[assignment]
            study._maybe_apply_profit_lock(
                symbol="GBPUSD",
                symbol_info=None,
                tickets=tickets,
                price=1.1000,
                spread_px=0.0,
                contract=contract,
                stats=stats,
            )
        finally:
            study.research_unit_pnl_usd = original_research_unit_pnl  # type: ignore[assignment]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_kind, "hedge")
        self.assertEqual(stats["realized_closes"], 1)
        self.assertEqual(stats["profit_lock_closes"], 1)
        self.assertAlmostEqual(stats["realized_net_usd"], 5.0)

    def test_profit_lock_does_not_add_admission_hedge_ticket(self) -> None:
        tickets = [study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1)]
        contract = study.SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=False,
            max_open_per_side=8,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="profit_lock",
            hedge_trigger_depth=0,
            hedge_profit_threshold_steps=2,
            variant_label="profit_lock_admission",
        )
        stats = {"opens": 1, "hedge_opens": 0}
        study._maybe_add_hedge_ticket(
            tickets=tickets,
            contract=contract,
            level_direction="SELL",
            entry_price=1.1020,
            bar_time=1,
            stats=stats,
        )
        self.assertEqual(len(tickets), 1)
        self.assertEqual(stats["hedge_opens"], 0)

    def test_score_key_booked_mode_prefers_higher_avg_close_at_equal_hourly(self) -> None:
        fast_low_avg = {
            "gross_positive_booked_usd_per_hour": 1.0,
            "avg_close_usd": 0.2,
            "realized_usd_per_hour": 1.0,
            "max_open_total": 10,
        }
        fast_high_avg = {
            "gross_positive_booked_usd_per_hour": 1.0,
            "avg_close_usd": 0.8,
            "realized_usd_per_hour": 1.0,
            "max_open_total": 30,
        }
        self.assertGreater(
            study.score_key(fast_high_avg, rank_mode="booked_usd_per_hour"),
            study.score_key(fast_low_avg, rank_mode="booked_usd_per_hour"),
        )

    def test_update_floating_stats_tracks_cover_violations(self) -> None:
        tickets = [study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1)]
        stats = {
            "realized_net_usd": 2.0,
            "min_floating_pnl_usd": 0.0,
            "max_floating_pnl_usd": 0.0,
            "min_combined_equity_usd": 0.0,
            "max_combined_equity_usd": 0.0,
            "min_realized_cover_gap_usd": 0.0,
            "min_combined_equity_delta_usd": 0.0,
            "realized_cover_violation_bars": 0,
            "max_used_margin_usd": 0.0,
            "min_free_margin_usd": 50.0,
            "min_margin_level_pct": float("inf"),
            "margin_stopout_bars": 0,
        }
        original_research_unit_pnl = study.research_unit_pnl_usd
        try:
            study.research_unit_pnl_usd = lambda symbol, direction, entry_price, close_price, spread_px, symbol_info: -7.0  # type: ignore[assignment]
            study._update_floating_stats(
                symbol="GBPUSD",
                symbol_info=None,
                tickets=tickets,
                price=1.1000,
                spread_px=0.0,
                stats=stats,
                starting_balance_usd=50.0,
                account_leverage=500.0,
                margin_stopout_level_pct=50.0,
            )
        finally:
            study.research_unit_pnl_usd = original_research_unit_pnl  # type: ignore[assignment]
        self.assertEqual(stats["min_realized_cover_gap_usd"], -5.0)
        self.assertEqual(stats["min_combined_equity_delta_usd"], -5.0)
        self.assertEqual(stats["realized_cover_violation_bars"], 1)

    def test_update_floating_stats_tracks_margin_stopout(self) -> None:
        tickets = [study.SnakeTicket(direction="SELL", entry_price=1.1020, opened_time=1)]
        stats = {
            "realized_net_usd": 0.0,
            "min_floating_pnl_usd": 0.0,
            "max_floating_pnl_usd": 0.0,
            "min_combined_equity_usd": 0.0,
            "max_combined_equity_usd": 0.0,
            "min_realized_cover_gap_usd": 0.0,
            "min_combined_equity_delta_usd": 0.0,
            "realized_cover_violation_bars": 0,
            "max_used_margin_usd": 0.0,
            "min_free_margin_usd": 50.0,
            "min_margin_level_pct": float("inf"),
            "margin_stopout_bars": 0,
        }
        original_research_unit_pnl = study.research_unit_pnl_usd
        try:
            study.research_unit_pnl_usd = lambda symbol, direction, entry_price, close_price, spread_px, symbol_info: -60.0  # type: ignore[assignment]
            study._update_floating_stats(
                symbol="GBPUSD",
                symbol_info=SimpleNamespace(trade_contract_size=100000, currency_profit="USD"),
                tickets=tickets,
                price=1.1000,
                spread_px=0.0,
                stats=stats,
                starting_balance_usd=50.0,
                account_leverage=500.0,
                margin_stopout_level_pct=50.0,
            )
        finally:
            study.research_unit_pnl_usd = original_research_unit_pnl  # type: ignore[assignment]
        self.assertLess(stats["min_free_margin_usd"], 0.0)
        self.assertLess(stats["min_margin_level_pct"], 50.0)
        self.assertEqual(stats["margin_stopout_bars"], 1)

    def test_filter_rows_applies_cover_and_floor_constraints(self) -> None:
        args = SimpleNamespace(
            max_mae_abs_usd=50.0,
            max_final_open=5,
            max_max_open=10,
            require_realized_cover=True,
            starting_balance_usd=100.0,
            hard_floor_usd=50.0,
            require_margin_survival=True,
            margin_stopout_level_pct=50.0,
        )
        rows = [
            {
                "variant_label": "survivor",
                "max_adverse_excursion_usd": -20.0,
                "final_open_count": 2,
                "max_open_total": 8,
                "min_realized_cover_gap_usd": 0.0,
                "min_combined_equity_delta_usd": -10.0,
                "min_free_margin_usd": 5.0,
                "min_margin_level_pct": 120.0,
                "margin_stopout_bars": 0,
            },
            {
                "variant_label": "cover_fail",
                "max_adverse_excursion_usd": -20.0,
                "final_open_count": 2,
                "max_open_total": 8,
                "min_realized_cover_gap_usd": -1.0,
                "min_combined_equity_delta_usd": -10.0,
                "min_free_margin_usd": 5.0,
                "min_margin_level_pct": 120.0,
                "margin_stopout_bars": 0,
            },
            {
                "variant_label": "floor_fail",
                "max_adverse_excursion_usd": -20.0,
                "final_open_count": 2,
                "max_open_total": 8,
                "min_realized_cover_gap_usd": 0.0,
                "min_combined_equity_delta_usd": -60.0,
                "min_free_margin_usd": 5.0,
                "min_margin_level_pct": 120.0,
                "margin_stopout_bars": 0,
            },
            {
                "variant_label": "margin_fail",
                "max_adverse_excursion_usd": -20.0,
                "final_open_count": 2,
                "max_open_total": 8,
                "min_realized_cover_gap_usd": 0.0,
                "min_combined_equity_delta_usd": -10.0,
                "min_free_margin_usd": -0.1,
                "min_margin_level_pct": 30.0,
                "margin_stopout_bars": 1,
            },
        ]
        kept = study.filter_rows(rows, args)
        self.assertEqual([row["variant_label"] for row in kept], ["survivor"])


if __name__ == "__main__":
    unittest.main()
