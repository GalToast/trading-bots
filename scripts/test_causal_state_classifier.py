#!/usr/bin/env python3
"""Tests for the causal state-space classifier (Gap 1)."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.causal_state_classifier import (
    CausalStateClassifier,
    STATE_HEALTHY_HARVEST,
    STATE_TOXIC_CONTINUATION,
    STATE_TEMPORARY_INVENTORY,
    STATE_TRAPPED_UNWIND,
    STATE_LIQUIDITY_THINNING,
    STATE_FAILED_RECLAIM,
    STATE_INSUFFICIENT_DATA,
)


def _make_close_event(**overrides):
    base = {
        "action": "close_ticket",
        "realized_pnl": 1.0,
        "hold_seconds": 30.0,
        "max_favorable_excursion_pnl": 2.0,
        "max_adverse_excursion_pnl": -0.5,
        "first_green_seen": True,
        "time_to_first_green_seconds": 5.0,
        "reclaimed_trigger_level_seen": True,
        "retraced_0_5x_step_seen": True,
        "spread_at_entry": 0.001,
    }
    base.update(overrides)
    return base


def _make_open_event(**overrides):
    base = {
        "action": "open_ticket",
        "regime_at_entry": "normal_reversion",
        "session_bucket_at_open": "good_session",
        "same_bar_open_burst_count_at_open": 0,
        "spread_at_entry": 0.001,
    }
    base.update(overrides)
    return base


def _write_events(events):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for e in events:
        tmp.write(json.dumps(e) + "\n")
    tmp.close()
    return tmp.name


def _write_state(state_dict):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(state_dict, tmp)
    tmp.close()
    return tmp.name


class TestCausalStateClassifier(unittest.TestCase):

    def test_insufficient_data_empty(self):
        path = _write_events([])
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertEqual(r.state, STATE_INSUFFICIENT_DATA)
        self.assertEqual(r.confidence, 1.0)

    def test_insufficient_data_single_close(self):
        path = _write_events([_make_close_event()])
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        # Should classify but with low confidence (< MIN_CLOSES_FOR_CONFIDENCE)
        self.assertTrue(r.confidence < 0.5)

    def test_healthy_harvest(self):
        events = [
            _make_close_event(realized_pnl=2.0, first_green_seen=True, hold_seconds=30.0)
            for _ in range(10)
        ]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertEqual(r.state, STATE_HEALTHY_HARVEST)
        self.assertTrue(r.confidence > 0.3)
        self.assertIn("Win rate", r.reason)

    def test_toxic_continuation(self):
        events = [
            _make_close_event(
                realized_pnl=-5.0,
                first_green_seen=False,
                max_favorable_excursion_pnl=0.5,
                max_adverse_excursion_pnl=-10.0,
                hold_seconds=300.0,
            )
            for _ in range(10)
        ]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertEqual(r.state, STATE_TOXIC_CONTINUATION)
        self.assertIn("toxic", r.reason.lower())

    def test_toxic_continuation_with_resets(self):
        events = [
            _make_close_event(
                realized_pnl=-3.0,
                first_green_seen=False,
                max_favorable_excursion_pnl=0.5,
                max_adverse_excursion_pnl=-8.0,
            )
            for _ in range(5)
        ]
        state = _write_state({
            "symbols": {"BTCUSD": {
                "anchor_resets": 5,
                "realized_net_usd": -15.0,
            }},
            "runner": {"consecutive_exceptions": 0},
        })
        c = CausalStateClassifier.from_state_file(state)
        # Override with events
        c.events = events
        r = c.classify()
        self.assertEqual(r.state, STATE_TOXIC_CONTINUATION)

    def test_liquidity_thinning(self):
        # Closes with high spread cost, some negative, wide-spread opens
        events = [
            _make_close_event(
                realized_pnl=-0.01,  # slightly negative after spread cost
                first_green_seen=False,
                max_favorable_excursion_pnl=0.02,
                max_adverse_excursion_pnl=-0.5,
                spread_at_entry=0.005,
                reclaimed_trigger_level_seen=False,
                hold_seconds=60.0,
            )
            for _ in range(3)
        ] + [
            _make_close_event(
                realized_pnl=0.01,  # barely positive
                first_green_seen=False,
                max_favorable_excursion_pnl=0.02,
                max_adverse_excursion_pnl=-0.5,
                spread_at_entry=0.005,
                reclaimed_trigger_level_seen=False,
                hold_seconds=60.0,
            )
            for _ in range(2)
        ]
        opens = [
            _make_open_event(
                regime_at_entry="wide_spread_stress",
                session_bucket_at_open="off_session",
                spread_at_entry=0.005,
            )
            for _ in range(10)
        ]
        events.extend(opens)
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertEqual(r.state, STATE_LIQUIDITY_THINNING)

    def test_trapped_position_unwind(self):
        # Mixed closes with burst concentration + some losses
        events = [
            _make_close_event(
                realized_pnl=0.5,
                regime_at_entry="clustered_expansion",
            )
            for _ in range(3)
        ] + [
            _make_close_event(
                realized_pnl=-1.0,
                first_green_seen=False,
                max_favorable_excursion_pnl=0.5,
                max_adverse_excursion_pnl=-3.0,
                regime_at_entry="clustered_expansion",
            )
            for _ in range(2)
        ]
        # Simulate burst opens
        opens = [
            _make_open_event(
                same_bar_open_burst_count_at_open=3,
                regime_at_entry="clustered_expansion",
            )
            for _ in range(10)
        ]
        events.extend(opens)
        c = CausalStateClassifier.from_event_log(_write_events(events))
        # Merge state snapshot with clustered open tickets
        c.state_snapshot = {
            "symbols": {"BTCUSD": {"anchor_resets": 0, "realized_net_usd": -0.5}},
            "open_tickets": [{"fill_price": 100.0, "direction": "BUY"} for _ in range(12)],
            "runner": {"consecutive_exceptions": 0},
        }
        r = c.classify()
        self.assertEqual(r.state, STATE_TRAPPED_UNWIND)

    def test_failed_reclaim(self):
        # Closes that sometimes reclaim and monetize, but mostly reclaim and lose
        # "Failed reclaim" = price returns to trigger (looks like recovery) but then reverses
        events = [
            # 3 wins: reclaimed and monetized
            _make_close_event(
                realized_pnl=1.0,
                first_green_seen=True,
                max_favorable_excursion_pnl=3.0,
                max_adverse_excursion_pnl=-1.0,
                reclaimed_trigger_level_seen=True,
                retraced_0_5x_step_seen=True,
                hold_seconds=60.0,
            )
            for _ in range(3)
        ] + [
            # 5 losses: reclaimed trigger but then reversed (false recovery)
            _make_close_event(
                realized_pnl=-3.0,
                first_green_seen=True,
                max_favorable_excursion_pnl=4.0,  # went +4 but closed at -3
                max_adverse_excursion_pnl=-5.0,
                reclaimed_trigger_level_seen=True,  # did reclaim
                retraced_0_5x_step_seen=True,
                hold_seconds=180.0,  # long hold — struggled
            )
            for _ in range(5)
        ]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertEqual(r.state, STATE_FAILED_RECLAIM)

    def test_temporary_inventory_displacement(self):
        events = [
            _make_close_event(
                realized_pnl=1.5,
                first_green_seen=True,
                time_to_first_green_seconds=3.0,
                reclaimed_trigger_level_seen=True,
                hold_seconds=20.0,
                max_favorable_excursion_pnl=3.0,
                max_adverse_excursion_pnl=-1.0,
            )
            for _ in range(8)
        ]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertEqual(r.state, STATE_TEMPORARY_INVENTORY)

    def test_state_scores_all_present(self):
        events = [_make_close_event() for _ in range(5)]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        for state in [STATE_HEALTHY_HARVEST, STATE_TOXIC_CONTINUATION,
                       STATE_TEMPORARY_INVENTORY, STATE_TRAPPED_UNWIND,
                       STATE_LIQUIDITY_THINNING, STATE_FAILED_RECLAIM,
                       STATE_INSUFFICIENT_DATA]:
            self.assertIn(state, r.state_scores)

    def test_recommended_control_action_present(self):
        events = [_make_close_event() for _ in range(5)]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertTrue(len(r.recommended_control_action) > 10)

    def test_falsification_read_present(self):
        events = [_make_close_event() for _ in range(5)]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        self.assertTrue(len(r.falsification_read) > 10)

    def test_gbpusd_live_event_log(self):
        """Integration test: classify the actual GBPUSD adaptive event log."""
        path = Path(__file__).parent.parent / "reports" / "penetration_lattice_shadow_gbpusd_m15_trend_harvest_v1_events.jsonl"
        if not path.exists():
            self.skipTest("GBPUSD event log not found")
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        # GBPUSD adaptive is a healthy lane: 100% WR, 0 resets, +$0.13/close
        self.assertEqual(r.state, STATE_HEALTHY_HARVEST)
        self.assertTrue(r.confidence > 0.2)

    def test_confidence_degraded_below_min_closes(self):
        """Below MIN_CLOSES_FOR_CONFIDENCE, confidence should be degraded."""
        events = [_make_close_event(realized_pnl=5.0) for _ in range(2)]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        r = c.classify()
        # Confidence should be scaled down
        self.assertTrue(r.confidence < 0.7)

    def test_mixed_regime_open_distribution(self):
        """Test that regime distribution is correctly computed."""
        events = [
            _make_close_event(),
            _make_open_event(regime_at_entry="burst_expansion"),
            _make_open_event(regime_at_entry="normal_reversion"),
            _make_open_event(regime_at_entry="wide_spread_stress"),
            _make_open_event(regime_at_entry="thin_off_session"),
        ]
        path = _write_events(events)
        c = CausalStateClassifier.from_event_log(path)
        features = c._extract_features()
        self.assertIsNotNone(features.regime_distribution)
        self.assertEqual(features.regime_distribution.get("burst_expansion"), 1)
        self.assertEqual(features.regime_distribution.get("wide_spread_stress"), 1)


if __name__ == "__main__":
    unittest.main()
