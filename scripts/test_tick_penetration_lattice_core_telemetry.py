#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tick_penetration_lattice_core as tick_core


class TickPenetrationLatticeCoreTelemetryTests(unittest.TestCase):
    def test_deserialize_tick_ticket_accepts_legacy_alias_fields(self) -> None:
        ticket = tick_core.deserialize_tick_ticket(
            {
                "direction": "buy",
                "entry_price": 100.0,
                "entry_fill_price": 100.25,
                "opened_time": 123,
                "opened_msc": 123456,
                "from_rearm": True,
                "position_comment": "legacy-payload",
            }
        )

        self.assertEqual(ticket.direction, "BUY")
        self.assertEqual(ticket.trigger_level, 100.0)
        self.assertEqual(ticket.fill_price, 100.25)
        self.assertEqual(ticket.opened_time, 123)
        self.assertEqual(ticket.opened_msc, 123456)
        self.assertTrue(ticket.from_rearm)
        self.assertEqual(ticket.position_comment, "legacy-payload")

    def test_initialize_ticket_telemetry_populates_entry_fields(self) -> None:
        ticket = tick_core.TickTicket(
            direction="BUY",
            trigger_level=100.0,
            fill_price=100.02,
            opened_time=10 * 3600,
            from_rearm=True,
        )
        tick = {"time": 10 * 3600, "time_msc": 10 * 3600 * 1000, "bid": 100.0, "ask": 100.2}
        tick["latest_tick_source_last"] = "symbol_info_tick"
        tick["tick_history_source_last"] = "copy_ticks_range"

        tick_core.initialize_ticket_telemetry(
            ticket,
            tick=tick,
            anchor=99.5,
            base_step_px=1.0,
            side_open_count=2,
            total_open_count=3,
            same_tick_open_burst_count=2,
            same_bar_open_burst_count=4,
        )

        self.assertEqual(ticket.session_bucket_at_open, "good_session")
        self.assertEqual(ticket.entry_context, "rearm|good_session|normal_spread")
        self.assertEqual(ticket.regime_at_entry, "burst_expansion")
        self.assertEqual(ticket.latest_tick_source_last, "symbol_info_tick")
        self.assertEqual(ticket.tick_history_source_last, "copy_ticks_range")
        self.assertAlmostEqual(ticket.spread_px_at_open, 0.2)
        self.assertEqual(ticket.base_step_px_at_open, 1.0)
        self.assertEqual(ticket.side_open_count_at_open, 2)
        self.assertEqual(ticket.total_open_count_at_open, 3)
        self.assertEqual(ticket.same_tick_open_burst_count_at_open, 2)
        self.assertEqual(ticket.same_bar_open_burst_count_at_open, 4)
        self.assertAlmostEqual(ticket.anchor_distance_px_at_open, 0.5)

    def test_update_ticket_path_metrics_tracks_excursions_and_threshold_flags(self) -> None:
        buy_ticket = tick_core.TickTicket(
            direction="BUY",
            trigger_level=100.0,
            fill_price=100.0,
            opened_time=1,
            base_step_px_at_open=4.0,
        )
        sell_ticket = tick_core.TickTicket(
            direction="SELL",
            trigger_level=100.0,
            fill_price=100.0,
            opened_time=1,
            base_step_px_at_open=4.0,
        )

        def fake_tick_pnl(_symbol: str, direction: str, entry: float, mark: float, volume: float = 0.01) -> float:
            raw = (mark - entry) if direction == "BUY" else (entry - mark)
            return round(raw * 10.0, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            tick_core.update_ticket_path_metrics(
                [buy_ticket, sell_ticket],
                symbol="ETHUSD",
                tick={"time": 10, "time_msc": 10001, "bid": 103.0, "ask": 97.0},
                volume=0.01,
            )
            tick_core.update_ticket_path_metrics(
                [buy_ticket, sell_ticket],
                symbol="ETHUSD",
                tick={"time": 12, "time_msc": 12001, "bid": 96.0, "ask": 104.0},
                volume=0.01,
            )

        self.assertEqual(buy_ticket.max_favorable_excursion_pnl, 30.0)
        self.assertEqual(buy_ticket.max_adverse_excursion_pnl, -40.0)
        self.assertEqual(buy_ticket.peak_pnl_before_exit, 30.0)
        self.assertTrue(buy_ticket.first_green_seen)
        self.assertEqual(buy_ticket.first_green_time, 10)
        self.assertEqual(buy_ticket.first_green_msc, 10001)
        self.assertTrue(buy_ticket.reclaimed_trigger_level_seen)
        self.assertTrue(buy_ticket.retraced_0_25x_step_seen)
        self.assertTrue(buy_ticket.retraced_0_5x_step_seen)

        self.assertEqual(sell_ticket.max_favorable_excursion_pnl, 30.0)
        self.assertEqual(sell_ticket.max_adverse_excursion_pnl, -40.0)
        self.assertEqual(sell_ticket.peak_pnl_before_exit, 30.0)
        self.assertTrue(sell_ticket.first_green_seen)
        self.assertEqual(sell_ticket.first_green_time, 10)
        self.assertEqual(sell_ticket.first_green_msc, 10001)
        self.assertTrue(sell_ticket.reclaimed_trigger_level_seen)
        self.assertTrue(sell_ticket.retraced_0_25x_step_seen)
        self.assertTrue(sell_ticket.retraced_0_5x_step_seen)

    def test_ticket_event_payload_emits_hold_and_path_fields(self) -> None:
        ticket = tick_core.TickTicket(
            direction="SELL",
            trigger_level=100.0,
            fill_price=100.0,
            opened_time=100,
            opened_msc=100000,
            from_rearm=True,
            base_step_px_at_open=2.0,
            spread_px_at_open=0.1254321,
            entry_context="rearm|off_session|wide_spread",
            session_bucket_at_open="off_session",
            regime_at_entry="thin_off_session",
            latest_tick_source_last="shared_price_cache",
            tick_history_source_last="shared_tick_cache",
            side_open_count_at_open=4,
            total_open_count_at_open=7,
            same_tick_open_burst_count_at_open=3,
            same_bar_open_burst_count_at_open=5,
            anchor_distance_px_at_open=0.8754321,
            max_favorable_excursion_pnl=12.3456,
            max_adverse_excursion_pnl=-6.7891,
            peak_pnl_before_exit=12.3456,
            first_green_seen=True,
            first_green_time=105,
            first_green_msc=105000,
            reclaimed_trigger_level_seen=True,
            retraced_0_25x_step_seen=True,
            retraced_0_5x_step_seen=False,
        )

        payload = tick_core.ticket_event_payload(
            ticket,
            tick={"time": 118, "time_msc": 118000, "bid": 99.25, "ask": 99.5},
            realized_pnl=-1.25,
            timeframe_name="M1",
        )

        self.assertEqual(payload["hold_seconds"], 18)
        self.assertEqual(payload["time_to_first_green_seconds"], 5)
        self.assertEqual(payload["rearm_to_first_green_seconds"], 5)
        self.assertEqual(payload["rearm_to_fail_seconds"], 18)
        self.assertEqual(payload["max_favorable_excursion_pnl"], 12.346)
        self.assertEqual(payload["max_adverse_excursion_pnl"], -6.789)
        self.assertEqual(payload["peak_pnl_before_exit"], 12.346)
        self.assertTrue(payload["first_green_before_fail"])
        self.assertEqual(payload["spread_at_entry"], 0.125432)
        self.assertEqual(payload["spread_at_exit"], 0.25)
        self.assertEqual(payload["entry_context"], "rearm|off_session|wide_spread")
        self.assertEqual(payload["session_bucket"], "off_session")
        self.assertEqual(payload["regime_at_entry"], "thin_off_session")
        self.assertEqual(payload["latest_tick_source_last"], "shared_price_cache")
        self.assertEqual(payload["tick_history_source_last"], "shared_tick_cache")
        self.assertEqual(payload["base_step_px_at_open"], 2.0)
        self.assertEqual(payload["anchor_distance_px_at_open"], 0.875432)
        self.assertEqual(payload["side_open_count_at_open"], 4)
        self.assertEqual(payload["total_open_count_at_open"], 7)
        self.assertEqual(payload["same_tick_open_burst_count_at_open"], 3)
        self.assertEqual(payload["same_bar_open_burst_count_at_open"], 5)
        self.assertTrue(payload["same_bar_round_trip"])
        self.assertTrue(payload["reclaimed_trigger_level_seen"])
        self.assertTrue(payload["retraced_0_25x_step_seen"])
        self.assertFalse(payload["retraced_0_5x_step_seen"])

    def test_guard_open_admission_blocks_same_side_expansion_until_recovery(self) -> None:
        symbol_info = SimpleNamespace(point=1.0, digits=2, spread=0.0)
        cfg = tick_core.RawConfig(
            step_pips=1.0,
            max_open_per_side=5,
            close_mode="two_level",
            step_is_price_units=True,
        )
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            cfg,
            symbol_info,
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            step_sell=1.0,
            step_buy=1.0,
            max_floating_loss_usd=-1000.0,
            guard_open_admission=True,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, tick_core.bucket_start(0, "M1"))

        def fake_tick_pnl(_symbol: str, direction: str, entry: float, mark: float, volume: float = 0.01) -> float:
            raw = (mark - entry) if direction == "BUY" else (entry - mark)
            return round(raw * 10.0, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            engine.process_tick({"time": 0, "time_msc": 1, "bid": 101.0, "ask": 101.2}, emit=False)
            self.assertEqual(len(engine.state.open_tickets), 1)

            engine.process_tick({"time": 1, "time_msc": 1001, "bid": 102.0, "ask": 102.2}, emit=False)
            self.assertEqual(len(engine.state.open_tickets), 1)

            engine.process_tick({"time": 2, "time_msc": 2001, "bid": 100.3, "ask": 100.5}, emit=False)
            engine.process_tick({"time": 3, "time_msc": 3001, "bid": 102.0, "ask": 102.2}, emit=False)

        self.assertEqual(len(engine.state.open_tickets), 2)
        self.assertTrue(engine.snapshot()["guard_open_admission"])
        first_ticket = tick_core.deserialize_tick_ticket(engine.state.open_tickets[0])
        self.assertTrue(first_ticket.first_green_seen)
        self.assertTrue(first_ticket.reclaimed_trigger_level_seen)

    def test_guard_open_admission_requires_frontier_recovery_not_old_inner_recovery(self) -> None:
        symbol_info = SimpleNamespace(point=1.0, digits=2, spread=0.0)
        cfg = tick_core.RawConfig(
            step_pips=1.0,
            max_open_per_side=5,
            close_mode="two_level",
            step_is_price_units=True,
        )
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            cfg,
            symbol_info,
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            step_sell=1.0,
            step_buy=1.0,
            max_floating_loss_usd=-1000.0,
            guard_open_admission=True,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, tick_core.bucket_start(0, "M1"))
        engine.state.next_sell_level = 103.0
        engine.state.open_tickets = [
            {
                "direction": "SELL",
                "trigger_level": 101.0,
                "fill_price": 101.0,
                "opened_time": 1,
                "opened_msc": 1000,
                "level_idx": 1,
                "first_green_seen": True,
                "reclaimed_trigger_level_seen": True,
            },
            {
                "direction": "SELL",
                "trigger_level": 102.0,
                "fill_price": 102.0,
                "opened_time": 2,
                "opened_msc": 2000,
                "level_idx": 2,
                "first_green_seen": False,
                "reclaimed_trigger_level_seen": False,
            },
        ]

        def fake_tick_pnl(_symbol: str, direction: str, entry: float, mark: float, volume: float = 0.01) -> float:
            raw = (mark - entry) if direction == "BUY" else (entry - mark)
            return round(raw * 10.0, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            engine.process_tick({"time": 3, "time_msc": 3000, "bid": 103.0, "ask": 103.2}, emit=False)

        self.assertEqual(len(engine.state.open_tickets), 2)
        trigger_levels = sorted(ticket["trigger_level"] for ticket in engine.state.open_tickets)
        self.assertEqual(trigger_levels, [101.0, 102.0])

    def test_burst_suppression_stops_additional_same_tick_opens(self) -> None:
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            tick_core.RawConfig(step_pips=1.0, max_open_per_side=10, close_mode="two_level", step_is_price_units=True),
            SimpleNamespace(point=1.0, digits=0, spread=0),
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            close_alpha=1.0,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            offensive_closure_enabled=False,
            suppress_additional_levels_after_burst=True,
            burst_open_threshold=2,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, 0)

        engine.process_tick(
            {"time": 60, "time_msc": 60000, "bid": 105.0, "ask": 105.2},
            emit=False,
        )

        self.assertEqual(len(engine.state.open_tickets), 2)
        self.assertEqual(engine.state.next_sell_level, 103.0)

    def test_burst_suppression_holds_for_rest_of_bar_then_resets_next_bar(self) -> None:
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            tick_core.RawConfig(step_pips=1.0, max_open_per_side=10, close_mode="two_level", step_is_price_units=True),
            SimpleNamespace(point=1.0, digits=0, spread=0),
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            close_alpha=1.0,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            offensive_closure_enabled=False,
            suppress_additional_levels_after_burst=True,
            burst_open_threshold=2,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, 0)

        engine.process_tick({"time": 60, "time_msc": 60000, "bid": 105.0, "ask": 105.2}, emit=False)
        engine.process_tick({"time": 70, "time_msc": 70000, "bid": 105.0, "ask": 105.2}, emit=False)
        self.assertEqual(len(engine.state.open_tickets), 2)
        self.assertEqual(engine.state.next_sell_level, 103.0)

        engine.process_tick({"time": 120, "time_msc": 120000, "bid": 105.0, "ask": 105.2}, emit=False)
        self.assertEqual(len(engine.state.open_tickets), 4)
        self.assertEqual(engine.state.next_sell_level, 105.0)

    def test_burst_suppression_is_same_side_only_and_keeps_reversal_entry_available(self) -> None:
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            tick_core.RawConfig(step_pips=1.0, max_open_per_side=10, close_mode="two_level", step_is_price_units=True),
            SimpleNamespace(point=1.0, digits=0, spread=0),
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            close_alpha=1.0,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            offensive_closure_enabled=False,
            suppress_additional_levels_after_burst=True,
            burst_open_threshold=2,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, 0)

        with patch.object(tick_core, "tick_pnl_usd", return_value=-5.0):
            engine.process_tick({"time": 60, "time_msc": 60000, "bid": 105.0, "ask": 105.2}, emit=False)
            engine.process_tick({"time": 70, "time_msc": 70000, "bid": 94.8, "ask": 95.0}, emit=False)

        buy_tickets = [ticket for ticket in engine.state.open_tickets if ticket["direction"] == "BUY"]
        self.assertEqual(len(buy_tickets), 2)
        self.assertEqual(sorted(ticket["trigger_level"] for ticket in buy_tickets), [98.0, 99.0])

    def test_adaptive_overlay_autopilot_arms_after_burst(self) -> None:
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            tick_core.RawConfig(step_pips=1.0, max_open_per_side=10, close_mode="two_level", step_is_price_units=True),
            SimpleNamespace(point=1.0, digits=0, spread=0),
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            close_alpha=1.0,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            offensive_closure_enabled=False,
            burst_open_threshold=2,
            adaptive_overlay_autopilot=True,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, 0)

        engine.process_tick(
            {"time": 60, "time_msc": 60000, "bid": 105.0, "ask": 105.2},
            emit=False,
        )

        snapshot = engine.snapshot()
        self.assertEqual(len(engine.state.open_tickets), 2)
        self.assertTrue(snapshot["adaptive_overlay_autopilot"])
        self.assertTrue(snapshot["adaptive_overlay_autopilot_triggered"])
        self.assertEqual(snapshot["adaptive_overlay_autopilot_reason"], "burst_concentration_detected")
        self.assertTrue(snapshot["guard_open_admission"])
        self.assertTrue(snapshot["cluster_aware_escape"])
        self.assertTrue(snapshot["suppress_additional_levels_after_burst"])

    def test_adaptive_overlay_autopilot_arms_after_toxic_first_path_close(self) -> None:
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            tick_core.RawConfig(step_pips=1.0, max_open_per_side=2, close_mode="two_level", step_is_price_units=True),
            SimpleNamespace(point=1.0, digits=0, spread=0),
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            close_alpha=1.0,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            burst_open_threshold=99,
            max_floating_loss_usd=-10.0,
            adaptive_overlay_autopilot=True,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, 0)

        def fake_tick_pnl(_symbol: str, direction: str, entry: float, mark: float, volume: float = 0.01) -> float:
            raw = (mark - entry) if direction == "BUY" else (entry - mark)
            return round(raw * 10.0, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            engine.process_tick({"time": 60, "time_msc": 60000, "bid": 94.8, "ask": 95.0}, emit=False)
            self.assertFalse(engine.snapshot()["adaptive_overlay_autopilot_triggered"])

            engine.process_tick({"time": 61, "time_msc": 61000, "bid": 90.0, "ask": 90.2}, emit=False)

        snapshot = engine.snapshot()
        self.assertTrue(snapshot["adaptive_overlay_autopilot_triggered"])
        self.assertEqual(snapshot["adaptive_overlay_autopilot_reason"], "first_path_never_green_toxic_continuation")
        self.assertEqual(snapshot["first_path_verdict"], "never_green_toxic_continuation")
        self.assertEqual(snapshot["first_path_close_action"], "forced_unwind")
        self.assertLess(snapshot["first_path_close_realized_pnl"], 0.0)
        self.assertTrue(snapshot["guard_open_admission"])
        self.assertTrue(snapshot["cluster_aware_escape"])
        self.assertTrue(snapshot["suppress_additional_levels_after_burst"])

    def test_cluster_aware_escape_removes_cluster_positions_without_list_discard_failure(self) -> None:
        engine = tick_core.TickStatefulRearmEngine(
            "BTCUSD",
            tick_core.RawConfig(step_pips=1.0, max_open_per_side=2, close_mode="two_level", step_is_price_units=True),
            SimpleNamespace(point=1.0, digits=0, spread=0),
            timeframe_name="M1",
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            close_alpha=1.0,
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            max_floating_loss_usd=-1000.0,
            escape_threshold_usd=5.0,
            cluster_aware_escape=True,
            allow_dynamic_geometry=False,
        )
        engine.prime(100.0, 0)

        def fake_tick_pnl(_symbol: str, direction: str, entry: float, mark: float, volume: float = 0.01) -> float:
            raw = (mark - entry) if direction == "BUY" else (entry - mark)
            return round(raw * 10.0, 3)

        with patch.object(tick_core, "tick_pnl_usd", side_effect=fake_tick_pnl):
            engine.process_tick({"time": 60, "time_msc": 60000, "bid": 94.8, "ask": 95.0}, emit=False)
            self.assertEqual(len(engine.state.open_tickets), 2)

            engine.process_tick({"time": 61, "time_msc": 61000, "bid": 90.0, "ask": 90.2}, emit=False)

        self.assertEqual(len(engine.state.open_tickets), 0)
        self.assertGreaterEqual(engine.state.realized_closes, 2)

    def test_generate_anticipatory_rearm_tokens_purges_misscaled_fx_tokens(self) -> None:
        engine = SimpleNamespace(
            variant=tick_core.REARM_VARIANTS["rearm_lvl2_exc1"],
            state=SimpleNamespace(
                open_tickets=[
                    {
                        "direction": "SELL",
                        "trigger_level": 1.355,
                        "fill_price": 1.355,
                        "opened_time": 1,
                        "from_rearm": False,
                    }
                ],
                anchor=1.35228,
            ),
            base_step_sell_px=0.000193,
        )
        tokens = [
            tick_core.TickRearmToken(
                direction="SELL",
                level=51.35732,
                level_idx=259093,
                armed=True,
                anticipatory=True,
                created_time=1,
            ),
            tick_core.TickRearmToken(
                direction="SELL",
                level=1.355,
                level_idx=14,
                armed=True,
                anticipatory=False,
                created_time=1,
            ),
        ]

        result = tick_core.TickStatefulRearmEngine._generate_anticipatory_rearm_tokens(
            engine,
            tokens,
            {"bid": 1.3548, "ask": 1.3549},
            2,
        )

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].anticipatory)
        self.assertAlmostEqual(result[0].level, 1.355)

    def test_entry_spread_allows_liquidity_gap_guard_for_raw_engine(self) -> None:
        engine = SimpleNamespace(
            max_entry_spread_ratio=0.0,
            liquidity_gap_spread_multiplier=2.0,
            liquidity_gap_spread_lookback=4,
            liquidity_gap_spread_floor_ratio=1.0,
            liquidity_gap_spread_max_ratio=0.0,
            _recent_entry_spread_ratios=tick_core.deque([0.2, 0.25, 0.3, 0.35], maxlen=8),
            _base_step_px=lambda _direction: 1.0,
        )
        engine._liquidity_gap_threshold_ratio = lambda: tick_core.TickStatefulRearmEngine._liquidity_gap_threshold_ratio(engine)

        allows, spread_px, spread_ratio, base_step_px, block_mode, baseline_ratio, threshold_ratio = (
            tick_core.TickStatefulRearmEngine._entry_spread_allows(
                engine,
                tick={"bid": 100.0, "ask": 101.2},
                direction="SELL",
            )
        )

        self.assertFalse(allows)
        self.assertAlmostEqual(spread_px, 1.2)
        self.assertAlmostEqual(spread_ratio, 1.2)
        self.assertAlmostEqual(base_step_px, 1.0)
        self.assertEqual(block_mode, "liquidity_gap")
        self.assertAlmostEqual(baseline_ratio, 0.275)
        self.assertAlmostEqual(threshold_ratio, 1.0)

    def test_entry_spread_allows_liquidity_gap_max_ratio_cap(self) -> None:
        engine = SimpleNamespace(
            max_entry_spread_ratio=0.0,
            liquidity_gap_spread_multiplier=20.0,
            liquidity_gap_spread_lookback=4,
            liquidity_gap_spread_floor_ratio=1.0,
            liquidity_gap_spread_max_ratio=4.0,
            _recent_entry_spread_ratios=tick_core.deque([0.2, 0.25, 0.3, 0.35], maxlen=8),
            _base_step_px=lambda _direction: 1.0,
        )
        engine._liquidity_gap_threshold_ratio = lambda: tick_core.TickStatefulRearmEngine._liquidity_gap_threshold_ratio(engine)

        allows, spread_px, spread_ratio, base_step_px, block_mode, baseline_ratio, threshold_ratio = (
            tick_core.TickStatefulRearmEngine._entry_spread_allows(
                engine,
                tick={"bid": 100.0, "ask": 104.5},
                direction="SELL",
            )
        )

        self.assertFalse(allows)
        self.assertAlmostEqual(spread_px, 4.5)
        self.assertAlmostEqual(spread_ratio, 4.5)
        self.assertAlmostEqual(base_step_px, 1.0)
        self.assertEqual(block_mode, "liquidity_gap")
        self.assertAlmostEqual(baseline_ratio, 0.275)
        self.assertAlmostEqual(threshold_ratio, 4.0)


if __name__ == "__main__":
    unittest.main()
