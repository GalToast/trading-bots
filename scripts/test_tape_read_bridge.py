#!/usr/bin/env python3
"""Tests for the tape-read bridge."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.tape_read_bridge import (
    build_tape_read,
    compute_burst_counts,
    compute_directional_bias,
    compute_realized_evidence,
    compute_same_bar_round_trip_rate,
    compute_spread_to_range_ratio_from_artifacts,
    compute_spread_to_step_ratio_from_artifacts,
)


class TestTapeReadBridge(unittest.TestCase):

    def test_directional_bias_from_events(self):
        """All buys should produce high directional bias."""
        events = [
            {"event": "open_ticket", "side": "buy", "timestamp": "2026-04-16T18:00:00"}
            for _ in range(10)
        ]
        bias = compute_directional_bias(events)
        self.assertIsNotNone(bias)
        self.assertAlmostEqual(bias, 1.0, places=1)

    def test_directional_bias_balanced(self):
        """50/50 buy/sell should produce zero bias."""
        events = [
            {"event": "open_ticket", "side": "buy", "timestamp": "2026-04-16T18:00:00"},
            {"event": "open_ticket", "side": "sell", "timestamp": "2026-04-16T18:01:00"},
            {"event": "open_ticket", "side": "buy", "timestamp": "2026-04-16T18:02:00"},
            {"event": "open_ticket", "side": "sell", "timestamp": "2026-04-16T18:03:00"},
        ]
        bias = compute_directional_bias(events)
        self.assertIsNotNone(bias)
        self.assertAlmostEqual(bias, 0.0, places=1)

    def test_directional_bias_reads_action_and_direction(self):
        """Live runner artifacts use action/direction rather than event/side."""
        events = [
            {"action": "open_ticket", "direction": "buy", "time_msc": 1000},
            {"action": "open_ticket", "direction": "buy", "time_msc": 2000},
            {"action": "open_ticket", "direction": "sell", "time_msc": 3000},
        ]
        bias = compute_directional_bias(events)
        self.assertIsNotNone(bias)
        self.assertAlmostEqual(bias, 1 / 3, places=2)

    def test_same_bar_round_trip_rate(self):
        """Closes within 60 seconds of open = same-bar round trip."""
        events = [
            {"event": "open_ticket", "ticket_id": "1", "timestamp": "2026-04-16T18:00:00"},
            {"event": "close_ticket", "ticket_id": "1", "timestamp": "2026-04-16T18:00:30"},  # same bar
            {"event": "open_ticket", "ticket_id": "2", "timestamp": "2026-04-16T18:01:00"},
            {"event": "close_ticket", "ticket_id": "2", "timestamp": "2026-04-16T18:05:00"},  # different bar
        ]
        rate = compute_same_bar_round_trip_rate(events)
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(rate, 0.5, places=1)

    def test_burst_counts(self):
        """Max same-bar and same-tick burst counts."""
        events = [
            {"event": "open_ticket", "timestamp": "2026-04-16T18:00:00"},
            {"event": "open_ticket", "timestamp": "2026-04-16T18:00:00"},  # same tick
            {"event": "open_ticket", "timestamp": "2026-04-16T18:00:30"},  # same bar, different tick
            {"event": "open_ticket", "timestamp": "2026-04-16T18:01:00"},  # different bar
        ]
        bar_burst, tick_burst = compute_burst_counts(events)
        self.assertEqual(bar_burst, 3)  # 3 in the 18:00 bar
        self.assertEqual(tick_burst, 2)  # 2 at exact 18:00:00

    def test_burst_counts_from_action_time_msc(self):
        """Live runner open events should count bursts from time_msc."""
        events = [
            {"action": "open_ticket", "time_msc": 1713280800000},
            {"action": "open_ticket", "time_msc": 1713280800000},
            {"action": "open_ticket", "time_msc": 1713280805000},
            {"action": "open_ticket", "time_msc": 1713280865000},
        ]
        bar_burst, tick_burst = compute_burst_counts(events)
        self.assertEqual(bar_burst, 3)
        self.assertEqual(tick_burst, 2)

    def test_realized_evidence_from_events(self):
        """Compute realized net from close events."""
        events = [
            {"event": "close_ticket", "realized_pnl": 5.0},
            {"event": "close_ticket", "realized_pnl": -2.0},
            {"event": "close_ticket", "realized_pnl": 3.0},
        ]
        evidence = compute_realized_evidence(events, {})
        self.assertEqual(evidence["realized_close_count"], 3)
        self.assertEqual(evidence["realized_net_usd"], 6.0)
        self.assertEqual(evidence["realized_avg_per_close"], 2.0)

    def test_realized_evidence_from_state(self):
        """State-level realized evidence overrides events when more complete."""
        events = [
            {"event": "close_ticket", "realized_pnl": 5.0},
        ]
        state = {
            "runner_session_trade_closes": 10,
            "runner_session_trade_realized_usd": 50.0,
        }
        evidence = compute_realized_evidence(events, state)
        self.assertEqual(evidence["realized_close_count"], 10)
        self.assertEqual(evidence["realized_net_usd"], 50.0)

    def test_realized_evidence_from_nested_symbol_state(self):
        """Per-symbol state rows should count as real realized evidence."""
        state = {
            "symbols": {
                "BTCUSD": {
                    "realized_closes": 14,
                    "realized_net_usd": -242.41,
                }
            }
        }
        evidence = compute_realized_evidence([], state)
        self.assertEqual(evidence["realized_close_count"], 14)
        self.assertEqual(evidence["realized_net_usd"], -242.41)
        self.assertEqual(evidence["realized_avg_per_close"], -17.32)

    def test_realized_evidence_reads_forced_unwind_actions(self):
        """Live runner forced_unwind actions are close-like realized evidence."""
        events = [
            {"action": "forced_unwind", "realized_pnl": -17.55},
            {"action": "forced_unwind", "realized_pnl": -17.55},
        ]
        evidence = compute_realized_evidence(events, {})
        self.assertEqual(evidence["realized_close_count"], 2)
        self.assertEqual(evidence["realized_net_usd"], -35.1)

    def test_quote_artifact_spread_ratios_from_nested_state(self):
        """Nested base-step state plus quote events should derive spread ratios."""
        state = {
            "symbols": {
                "BTCUSD": {
                    "base_step_px": 15.0,
                }
            }
        }
        events = [
            {"action": "tick_history_fallback", "bid": 100.0, "ask": 110.0, "time_msc": 1000},
            {"action": "tick_history_fallback", "bid": 90.0, "ask": 105.0, "time_msc": 2000},
            {"action": "tick_history_fallback", "bid": 80.0, "ask": 95.0, "time_msc": 3000},
        ]
        self.assertAlmostEqual(compute_spread_to_step_ratio_from_artifacts(state, events), 1.0, places=3)
        self.assertAlmostEqual(compute_spread_to_range_ratio_from_artifacts(events), 0.8571428571, places=3)

    def test_full_tape_read_micro_harvest(self):
        """Quiet tape with high round-trip rate should produce micro_harvest."""
        state = {
            "regime": "mixed",
            "spread_to_step_ratio": 0.15,
            "spread_to_range_ratio": 0.30,
        }
        events = [
            {"event": "open_ticket", "ticket_id": "1", "side": "buy", "timestamp": "2026-04-16T18:00:00"},
            {"event": "close_ticket", "ticket_id": "1", "timestamp": "2026-04-16T18:00:30"},  # same bar
            {"event": "open_ticket", "ticket_id": "2", "side": "sell", "timestamp": "2026-04-16T18:01:00"},
            {"event": "close_ticket", "ticket_id": "2", "timestamp": "2026-04-16T18:01:30"},  # same bar
        ]
        tape_read = build_tape_read(state, events, "GBPUSD")
        self.assertEqual(tape_read["profit_mode"], "micro_harvest")

    def test_full_tape_read_trend(self):
        """Strong directional bias in trending regime should produce trend_harvest."""
        state = {
            "regime": "trending",
            "current_atr": 280.0,
        }
        events = [
            {"event": "open_ticket", "side": "buy", "timestamp": f"2026-04-16T18:{minute:02d}:00"}
            for minute in range(10)
        ]
        tape_read = build_tape_read(state, events, "BTCUSD")
        self.assertEqual(tape_read["profit_mode"], "trend_harvest")

    def test_full_tape_read_toxic_flow(self):
        """Burst concentration should produce guarded_toxic_flow."""
        state = {
            "regime": "mixed",
        }
        events = [
            {"event": "open_ticket", "timestamp": "2026-04-16T18:00:00"}
            for _ in range(5)
        ]
        tape_read = build_tape_read(state, events, "BTCUSD")
        self.assertEqual(tape_read["profit_mode"], "guarded_toxic_flow")

    def test_full_tape_read_cash_repair(self):
        """No closes, negative net should produce cash_repair_harvest."""
        state = {
            "regime": "mixed",
            "runner_session_trade_closes": 0,
            "runner_session_trade_realized_usd": -17.77,
        }
        tape_read = build_tape_read(state, [], "BTCUSD")
        self.assertEqual(tape_read["profit_mode"], "cash_repair_harvest")

    def test_full_tape_read_live_action_artifact_derives_spread_and_burst(self):
        """Live action-based artifacts should no longer collapse to null spread/burst fields."""
        state = {
            "regime": "mixed",
            "symbols": {
                "BTCUSD": {
                    "base_step_px": 15.0,
                    "realized_closes": 14,
                    "realized_net_usd": -242.41,
                }
            },
        }
        events = [
            {"action": "tick_history_fallback", "bid": 74235.25, "ask": 74410.77, "time_msc": 1776208055756},
            {"action": "open_ticket", "direction": "sell", "bid": 74235.25, "ask": 74410.77, "time_msc": 1776208055756},
            {"action": "open_ticket", "direction": "sell", "bid": 74235.25, "ask": 74410.77, "time_msc": 1776208055756},
            {"action": "forced_unwind", "direction": "sell", "realized_pnl": -17.55, "bid": 74235.25, "ask": 74410.77, "time_msc": 1776208055756},
        ]
        tape_read = build_tape_read(state, events, "BTCUSD")
        self.assertEqual(tape_read["realized_evidence"]["realized_close_count"], 14)
        self.assertLess(tape_read["tape_signals"]["spread_to_step_ratio"], 20.0)
        self.assertGreater(tape_read["tape_signals"]["spread_to_step_ratio"], 10.0)
        self.assertEqual(tape_read["tape_signals"]["same_tick_open_burst_count"], 2)


if __name__ == "__main__":
    unittest.main()
