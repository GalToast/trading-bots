#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_coinbase_spot_machinegun_shadow import MachinegunShadowEngine


class MachinegunShadowTests(unittest.TestCase):
    def test_open_and_profit_trail_close_counts_true_fees(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            engine.open_position(
                {
                    "product_id": "HOT-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 1.0,
                    "edge_over_hurdle_pct": 5.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            self.assertIsNotNone(engine.position)
            self.assertAlmostEqual(engine.total_fees, 0.48)
            engine.maybe_close_position({"bid": 1.10}, event_path=event_path)
            self.assertIsNotNone(engine.position)
            engine.maybe_close_position({"bid": 1.085}, event_path=event_path)
            self.assertIsNone(engine.position)
            self.assertEqual(engine.realized_closes, 1)
            self.assertGreater(engine.realized_net_usd, 0)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["exit_reason"], "profit_trail")
            self.assertEqual(events[-1]["fee_bps_per_side"], 120.0)

    def test_rotation_requires_fee_and_buffer_edge_advantage(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
        )
        engine.target_net_pct_per_hour = 0.0
        engine.position = None
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine.open_position(
                {
                    "product_id": "HOLD-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 1.0,
                    "edge_over_hurdle_pct": 1.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
        mark = engine.mark_position({"bid": 1.02})
        weak = engine.evaluate_rotation(
            [
                {"rank": 1, "product_id": "NEXT-USD", "edge_over_hurdle_pct": 3.8, "playbook": "hot_potato_hour_rotation"},
                {"rank": 2, "product_id": "HOLD-USD", "edge_over_hurdle_pct": 1.0, "playbook": "fee_hurdle_breakout_trailer"},
            ],
            mark,
        )
        self.assertEqual(weak["decision"], "hold_challenger_not_fee_clear")
        self.assertAlmostEqual(weak["rotation_required_pct"], 2.9)
        strong = engine.evaluate_rotation(
            [
                {"rank": 1, "product_id": "NEXT-USD", "edge_over_hurdle_pct": 4.0, "playbook": "hot_potato_hour_rotation"},
                {"rank": 2, "product_id": "HOLD-USD", "edge_over_hurdle_pct": 1.0, "playbook": "fee_hurdle_breakout_trailer"},
            ],
            mark,
        )
        self.assertEqual(strong["decision"], "rotate_to_challenger")

    def test_target_pressure_blocks_red_rotation_even_when_challenger_clears(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=1,
        )
        engine.target_started_at = "2026-04-23T00:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine.open_position(
                {
                    "product_id": "HOLD-USD",
                    "playbook": "hot_potato_hour_rotation",
                    "trail_giveback_pct": 1.0,
                    "edge_over_hurdle_pct": 1.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
        mark = engine.mark_position({"bid": 1.0})
        decision = engine.evaluate_rotation(
            [
                {"rank": 1, "product_id": "NEXT-USD", "edge_over_hurdle_pct": 5.0, "playbook": "fee_hurdle_breakout_trailer"},
                {"rank": 2, "product_id": "HOLD-USD", "edge_over_hurdle_pct": 1.0, "playbook": "hot_potato_hour_rotation"},
            ],
            mark,
        )
        self.assertEqual(decision["decision"], "hold_current_red_no_rotation")

    def test_fee_adjusted_net_loss_triggers_emergency_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            engine.open_position(
                {
                    "product_id": "HOT-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 1.0,
                    "edge_over_hurdle_pct": 5.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 0.98}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "emergency_net_loss")
            self.assertEqual(engine.reentry_blocks["HOT-USD"], 3)

    def test_fee_paid_profit_lock_closes_when_net_profit_retraces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            engine.open_position(
                {
                    "product_id": "HOT-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 10.0,
                    "edge_over_hurdle_pct": 5.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 1.05}, event_path=event_path)
            self.assertIsNotNone(engine.position)
            self.assertGreater(engine.position.max_net_pnl, 0.0)
            engine.maybe_close_position({"bid": 1.03}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "fee_paid_profit_lock")
            self.assertGreater(close_event["net_pnl"], 0.0)
            self.assertGreater(close_event["max_net_pnl"], close_event["net_pnl"])

    def test_close_event_reports_mfe_capture_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=0.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            engine.open_position(
                {
                    "product_id": "HOT-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 20.0,
                    "edge_over_hurdle_pct": 5.0,
                    "fast_green_prob": 0.97,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 1.10}, event_path=event_path)
            self.assertIsNotNone(engine.position)
            mark = engine.mark_position({"bid": 1.05})
            engine.close_position(mark, event_path=event_path, exit_reason="test_capture")
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["mfe_gross_pct"], 10.0)
            self.assertAlmostEqual(close_event["gross_mfe_capture_pct"], 50.0, places=3)
            self.assertGreater(close_event["net_mfe_capture_pct"], 0.0)
            self.assertLess(close_event["net_mfe_capture_pct"], 100.0)
            self.assertEqual(close_event["entry_fast_green_prob"], 0.97)

    def test_eligible_rows_skips_reentry_cooldown(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
        )
        engine.reentry_blocks["HOT-USD"] = 2
        rows = [
            {"product_id": "HOT-USD", "machinegun_score": 10},
            {"product_id": "NEXT-USD", "machinegun_score": 8},
        ]
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "NEXT-USD")

    def test_eligible_rows_requires_entry_confirmation_streak(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=2,
        )
        rows = [{"product_id": "HOT-USD", "machinegun_score": 10}]
        engine.refresh_candidate_streaks(rows)
        self.assertEqual(engine.eligible_rows(rows), [])
        engine.refresh_candidate_streaks(rows)
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "HOT-USD")

    def test_eligible_rows_requires_fast_green_threshold_when_enabled(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=0.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=1,
            require_fast_green_prob=0.95,
        )
        rows = [
            {"product_id": "LOW-USD", "machinegun_score": 10, "fast_green_prob": 0.949},
            {"product_id": "MISSING-USD", "machinegun_score": 9},
            {"product_id": "HOT-USD", "machinegun_score": 8, "fast_green_prob": 0.951},
        ]
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "HOT-USD")

    def test_eligible_rows_requires_bubble_capture_threshold_when_enabled(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=0.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=1,
            require_bubble_capture_net_pct_per_hour=0.25,
        )
        rows = [
            {"product_id": "LOW-USD", "machinegun_score": 10, "bubble_capture_net_pct_per_hour": 0.249},
            {"product_id": "MISSING-USD", "machinegun_score": 9},
            {"product_id": "HOT-USD", "machinegun_score": 8, "bubble_capture_net_pct_per_hour": 0.251},
        ]
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "HOT-USD")

    def test_target_pressure_requires_stronger_entry_edge_when_behind(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=1,
            target_pressure_min_entry_edge_pct=3.0,
        )
        engine.target_started_at = "2026-04-23T00:00:00+00:00"
        rows = [
            {"product_id": "WEAK-USD", "machinegun_score": 10, "ghost_adjusted_edge_over_hurdle_pct": 2.4},
            {"product_id": "STRONG-USD", "machinegun_score": 8, "ghost_adjusted_edge_over_hurdle_pct": 3.2},
        ]
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "STRONG-USD")

    def test_target_pressure_requires_live_move_when_behind(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=1,
            target_pressure_min_entry_edge_pct=3.0,
            target_pressure_min_live_move_bps=5.0,
        )
        engine.target_started_at = "2026-04-23T00:00:00+00:00"
        rows = [{"product_id": "HOT-USD", "ghost_adjusted_edge_over_hurdle_pct": 4.0}]
        engine.update_live_momentum({"HOT-USD": {"bid": 1.0, "ask": 1.001}})
        self.assertEqual(engine.eligible_rows(rows), [])
        engine.update_live_momentum({"HOT-USD": {"bid": 1.0002, "ask": 1.0012}})
        self.assertEqual(engine.eligible_rows(rows), [])
        engine.update_live_momentum({"HOT-USD": {"bid": 1.001, "ask": 1.002}})
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "HOT-USD")

    def test_target_pressure_requires_sustained_live_move_confirmation(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=2,
            target_pressure_min_entry_edge_pct=3.0,
            target_pressure_min_live_move_bps=5.0,
        )
        engine.target_started_at = "2026-04-23T00:00:00+00:00"
        rows = [{"product_id": "HOT-USD", "ghost_adjusted_edge_over_hurdle_pct": 4.0}]
        engine.refresh_candidate_streaks(rows)
        engine.refresh_candidate_streaks(rows)
        engine.update_live_momentum({"HOT-USD": {"bid": 1.0, "ask": 1.001}})
        engine.update_live_momentum({"HOT-USD": {"bid": 1.001, "ask": 1.002}})
        self.assertEqual(engine.eligible_rows(rows), [])
        engine.update_live_momentum({"HOT-USD": {"bid": 1.002, "ask": 1.003}})
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "HOT-USD")

    def test_live_velocity_override_can_admit_moving_coin_below_pressure_edge_floor(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
            entry_confirmation_polls=1,
            target_pressure_min_entry_edge_pct=3.0,
            target_pressure_min_live_move_bps=5.0,
            target_pressure_live_override_bps=12.0,
            target_pressure_live_override_min_edge_pct=1.25,
        )
        engine.target_started_at = "2026-04-23T00:00:00+00:00"
        row = {
            "product_id": "MOVE-USD",
            "ghost_adjusted_edge_over_hurdle_pct": 1.5,
            "ret_15m_pct": 2.0,
            "ret_60m_pct": 3.0,
        }
        engine.update_live_momentum({"MOVE-USD": {"bid": 1.0, "ask": 1.001}})
        engine.update_live_momentum({"MOVE-USD": {"bid": 1.002, "ask": 1.003}})
        self.assertEqual(engine.eligible_rows([row])[0]["product_id"], "MOVE-USD")

    def test_target_pressure_banks_profit_on_below_floor_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=85.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
                target_pressure_min_entry_edge_pct=3.0,
            )
            engine.target_started_at = "2026-04-23T00:00:00+00:00"
            engine.open_position(
                {
                    "product_id": "WEAK-USD",
                    "playbook": "hot_potato_hour_rotation",
                    "trail_giveback_pct": 10.0,
                    "edge_over_hurdle_pct": 2.4,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 1.026}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "target_pressure_profit_bank")
            self.assertGreater(close_event["net_pnl"], 0.0)

    def test_target_pressure_cuts_below_floor_hold_after_trail_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=85.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
                target_pressure_min_entry_edge_pct=3.0,
            )
            engine.target_started_at = "2026-04-23T00:00:00+00:00"
            engine.open_position(
                {
                    "product_id": "WEAK-USD",
                    "playbook": "hot_potato_hour_rotation",
                    "trail_giveback_pct": 1.0,
                    "edge_over_hurdle_pct": 2.4,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 1.01}, event_path=event_path)
            self.assertIsNotNone(engine.position)
            engine.maybe_close_position({"bid": 0.999}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "target_pressure_weak_edge_trail_exit")
            self.assertEqual(engine.reentry_blocks["WEAK-USD"], 3)

    def test_target_pressure_cuts_below_floor_hold_at_pressure_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=85.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
                target_pressure_min_entry_edge_pct=3.0,
            )
            engine.target_started_at = "2026-04-23T00:00:00+00:00"
            engine.open_position(
                {
                    "product_id": "WEAK-USD",
                    "playbook": "hot_potato_hour_rotation",
                    "trail_giveback_pct": 10.0,
                    "edge_over_hurdle_pct": 2.4,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 1.0}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "target_pressure_weak_edge_loss_exit")
            self.assertGreater(close_event["net_pct_on_cost"], -4.0)

    def test_eligible_rows_waits_for_reclaim_after_bad_timing_ghosts(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=0,
            ghost_min_closes_for_bias=3,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
        )
        engine.ghost_stats["HOT-USD"] = {"closes": 3, "wins": 0, "losses": 3, "net_pct": -12.0}
        rows = [
            {"product_id": "HOT-USD", "machinegun_score": 10},
            {"product_id": "NEXT-USD", "machinegun_score": 8},
        ]
        self.assertEqual(engine.eligible_rows(rows)[0]["product_id"], "NEXT-USD")
        self.assertIn("ghost_timing_cooloff", engine.ghost_timing_cooloff_reason("HOT-USD"))
        engine.ghost_positions["HOT-USD"] = type("GhostStub", (), {"highest_bid": 1.0})()
        self.assertEqual(engine.eligible_rows(rows, {"HOT-USD": {"bid": 1.01}})[0]["product_id"], "HOT-USD")

    def test_target_pressure_cuts_bad_timing_entry_before_full_emergency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            engine.target_started_at = "2026-04-23T00:00:00+00:00"
            engine.ghost_stats["HOT-USD"] = {"closes": 3, "wins": 0, "losses": 3, "net_pct": -12.0}
            engine.open_position(
                {
                    "product_id": "HOT-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 10.0,
                    "edge_over_hurdle_pct": 5.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.maybe_close_position({"bid": 0.99}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "target_pressure_timing_cooloff_exit")
            self.assertGreater(close_event["net_pct_on_cost"], -4.0)
            self.assertEqual(engine.reentry_blocks["HOT-USD"], 3)

    def test_target_pressure_cuts_losing_entry_when_live_momentum_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=0,
                ghost_min_closes_for_bias=3,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=85.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
                target_pressure_min_live_move_bps=5.0,
            )
            engine.target_started_at = "2026-04-23T00:00:00+00:00"
            engine.open_position(
                {
                    "product_id": "HOT-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 10.0,
                    "edge_over_hurdle_pct": 5.0,
                },
                {"ask": 1.0, "bid": 1.0},
                event_path=event_path,
            )
            engine.live_momentum["HOT-USD"] = {"move_bps": -10.0, "samples": 2}
            engine.maybe_close_position({"bid": 0.99}, event_path=event_path)
            self.assertIsNone(engine.position)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_shadow")
            self.assertEqual(close_event["exit_reason"], "target_pressure_live_momentum_failed_exit")
            self.assertGreater(close_event["net_pct_on_cost"], -4.0)

    def test_ghost_tournament_closes_and_scores_normalized_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=2,
                ghost_min_closes_for_bias=1,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            rows = [
                {
                    "product_id": "A-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 1.0,
                    "edge_over_hurdle_pct": 3.0,
                }
            ]
            engine.update_ghost_tournament(rows, {"A-USD": {"ask": 1.0, "bid": 1.0}}, event_path=event_path)
            self.assertIn("A-USD", engine.ghost_positions)
            engine.update_ghost_tournament(rows, {"A-USD": {"ask": 1.1, "bid": 1.1}}, event_path=event_path)
            self.assertIn("A-USD", engine.ghost_positions)
            engine.update_ghost_tournament(rows, {"A-USD": {"ask": 1.08, "bid": 1.08}}, event_path=event_path)
            self.assertNotIn("A-USD", engine.ghost_positions)
            self.assertEqual(engine.ghost_stats["A-USD"]["closes"], 1)
            self.assertGreater(engine.ghost_stats["A-USD"]["net_pct"], 0.0)

    def test_ghost_tournament_uses_fee_paid_profit_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = MachinegunShadowEngine(
                starting_cash_usd=50.0,
                deploy_pct=0.8,
                taker_fee_bps=120.0,
                min_quote_usd=5.0,
                max_loss_pct=4.0,
                min_profit_to_trail_usd=0.01,
                rotation_buffer_pct=0.5,
                reentry_cooldown_polls=3,
                ghost_top_n=1,
                ghost_min_closes_for_bias=1,
                ghost_edge_bias_cap_pct=2.0,
                profit_lock_retention_pct=35.0,
                target_net_pct_per_hour=5.0,
                ghost_timing_cooloff_min_closes=3,
                ghost_timing_cooloff_max_avg_loss_pct=3.0,
                target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
            )
            rows = [
                {
                    "product_id": "A-USD",
                    "playbook": "fee_hurdle_breakout_trailer",
                    "trail_giveback_pct": 10.0,
                    "edge_over_hurdle_pct": 3.0,
                }
            ]
            engine.update_ghost_tournament(rows, {"A-USD": {"ask": 1.0, "bid": 1.0}}, event_path=event_path)
            engine.update_ghost_tournament(rows, {"A-USD": {"ask": 1.05, "bid": 1.05}}, event_path=event_path)
            self.assertIn("A-USD", engine.ghost_positions)
            self.assertGreater(engine.ghost_positions["A-USD"].max_net_pnl, 0.0)
            engine.update_ghost_tournament(rows, {"A-USD": {"ask": 1.03, "bid": 1.03}}, event_path=event_path)
            self.assertNotIn("A-USD", engine.ghost_positions)
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            close_event = next(event for event in events if event.get("action") == "close_machinegun_ghost")
            self.assertEqual(close_event["exit_reason"], "ghost_fee_paid_profit_lock")
            self.assertGreater(close_event["net_pnl"], 0.0)
            self.assertGreater(close_event["max_net_pnl"], close_event["net_pnl"])

    def test_ghost_bias_reorders_candidates_after_minimum_closes(self) -> None:
        engine = MachinegunShadowEngine(
            starting_cash_usd=50.0,
            deploy_pct=0.8,
            taker_fee_bps=120.0,
            min_quote_usd=5.0,
            max_loss_pct=4.0,
            min_profit_to_trail_usd=0.01,
            rotation_buffer_pct=0.5,
            reentry_cooldown_polls=3,
            ghost_top_n=2,
            ghost_min_closes_for_bias=2,
            ghost_edge_bias_cap_pct=2.0,
            profit_lock_retention_pct=35.0,
            target_net_pct_per_hour=5.0,
            ghost_timing_cooloff_min_closes=3,
            ghost_timing_cooloff_max_avg_loss_pct=3.0,
            target_pressure_exit_net_loss_pct=2.0,
                entry_confirmation_polls=1,
        )
        engine.ghost_stats["RAW-USD"] = {"closes": 2, "wins": 0, "losses": 2, "net_pct": -4.0}
        engine.ghost_stats["GHOST-USD"] = {"closes": 2, "wins": 2, "losses": 0, "net_pct": 4.0}
        rows = [
            {"rank": 1, "product_id": "RAW-USD", "machinegun_score": 10.0, "edge_over_hurdle_pct": 3.0},
            {"rank": 2, "product_id": "GHOST-USD", "machinegun_score": 9.0, "edge_over_hurdle_pct": 2.5},
        ]
        adjusted = engine.ghost_adjusted_rows(rows)
        self.assertEqual(adjusted[0]["product_id"], "GHOST-USD")
        self.assertEqual(adjusted[0]["raw_rank"], 2)
        self.assertEqual(adjusted[0]["ghost_edge_bias_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
