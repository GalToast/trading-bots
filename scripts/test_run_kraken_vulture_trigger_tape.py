#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_vulture_trigger_tape as tape


class KrakenVultureTriggerTapeTests(unittest.TestCase):
    def test_buy_fill_for_quote_uses_depth_vwap(self) -> None:
        fill = tape.buy_fill_for_quote(
            [tape.Level(price=1.0, size=5.0), tape.Level(price=2.0, size=10.0)],
            9.0,
        )

        self.assertTrue(fill.ok)
        self.assertAlmostEqual(fill.qty, 7.0)
        self.assertAlmostEqual(fill.gross_quote, 9.0)
        self.assertAlmostEqual(fill.avg_price, 9.0 / 7.0)

    def test_sell_fill_for_qty_blocks_when_bid_depth_insufficient(self) -> None:
        fill = tape.sell_fill_for_qty([tape.Level(price=2.0, size=1.0)], 2.0)

        self.assertFalse(fill.ok)
        self.assertEqual(fill.reason, "insufficient_bid_depth")
        self.assertAlmostEqual(fill.gross_quote, 2.0)

    def test_net_bps_for_exit(self) -> None:
        self.assertAlmostEqual(tape.net_bps_for_exit(100.0, 101.0), 100.0)
        self.assertAlmostEqual(tape.net_bps_for_exit(100.0, 99.0), -100.0)

    def test_new_dump_trigger_uses_prior_high_excluding_current_sample(self) -> None:
        samples = tape.deque(
            [
                {"ts": 1.0, "bid": 100.0, "ask": 101.0},
                {"ts": 2.0, "bid": 99.0, "ask": 100.0},
                {"ts": 3.0, "bid": 95.0, "ask": 96.0},
            ],
            maxlen=3,
        )
        args = type("Args", (), {"lookback_samples": 2, "min_dump_bps": 400.0})()

        trigger = tape.new_dump_trigger(
            product_id="TEST-USD",
            book=tape.Book(bid=95.0, ask=96.0, bids=[], asks=[]),
            samples=samples,
            args=args,
        )

        self.assertIsNotNone(trigger)
        assert trigger is not None
        self.assertEqual(trigger["prior_high_bid"], 100.0)
        self.assertAlmostEqual(trigger["dump_bps"], -500.0)

    def test_update_reclaim_trigger_tracks_lower_low_then_bounce(self) -> None:
        trigger = {"low_bid": 95.0}

        tape.update_reclaim_trigger(trigger, tape.Book(bid=90.0, ask=91.0, bids=[], asks=[]))
        self.assertEqual(trigger["low_bid"], 90.0)
        self.assertEqual(trigger["reclaim_bps"], 0.0)

        tape.update_reclaim_trigger(trigger, tape.Book(bid=92.0, ask=93.0, bids=[], asks=[]))
        self.assertEqual(trigger["low_bid"], 90.0)
        self.assertGreater(trigger["reclaim_bps"], 200.0)

    def test_maybe_open_position_arms_then_waits_for_reclaim(self) -> None:
        samples = tape.deque(
            [
                {"ts": 1.0, "bid": 100.0, "ask": 101.0},
                {"ts": 2.0, "bid": 99.0, "ask": 100.0},
                {"ts": 3.0, "bid": 95.0, "ask": 96.0},
            ],
            maxlen=3,
        )
        args = type(
            "Args",
            (),
            {
                "lookback_samples": 2,
                "min_dump_bps": 400.0,
                "entry_trigger_mode": "reclaim",
                "reclaim_bps": 50.0,
                "reclaim_timeout_seconds": 120.0,
            },
        )()
        pending: dict[str, dict] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            position, action = tape.maybe_open_position(
                product_id="TEST-USD",
                pair=object(),
                book=tape.Book(bid=95.0, ask=96.0, bids=[], asks=[]),
                samples=samples,
                pending_triggers=pending,
                args=args,
                event_path=Path(tmpdir) / "events.jsonl",
            )

        self.assertIsNone(position)
        self.assertEqual(action, "reclaim_armed")
        self.assertIn("TEST-USD", pending)

    def test_open_position_blocks_reclaim_that_does_not_clear_spread_floor(self) -> None:
        args = type(
            "Args",
            (),
            {
                "entry_trigger_mode": "reclaim",
                "deploy_usd": 15.0,
                "taker_fee_bps": 40.0,
                "max_spread_bps": 500.0,
                "min_reclaim_after_spread_bps": 50.0,
                "min_reclaim_after_cost_bps": 0.0,
                "min_low_age_seconds": 0.0,
                "max_low_age_seconds": 0.0,
                "min_reclaim_velocity_bps_per_second": 0.0,
            },
        )()
        trigger = {"dump_bps": -100.0, "reclaim_bps": 40.0, "low_bid": 99.0}
        pair = type("Pair", (), {"cost_min": 1.0, "order_min": 0.0001})()
        book = tape.Book(
            bid=100.0,
            ask=101.0,
            bids=[tape.Level(price=100.0, size=10.0)],
            asks=[tape.Level(price=101.0, size=10.0)],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            position = tape.open_position_from_trigger(
                product_id="TEST-USD",
                pair=pair,
                book=book,
                trigger=trigger,
                args=args,
                event_path=Path(tmpdir) / "events.jsonl",
            )

        self.assertIsNone(position)

    def test_open_position_blocks_wick_low_before_stabilization(self) -> None:
        args = type(
            "Args",
            (),
            {
                "entry_trigger_mode": "reclaim",
                "deploy_usd": 15.0,
                "taker_fee_bps": 40.0,
                "max_spread_bps": 500.0,
                "min_reclaim_after_spread_bps": 0.0,
                "min_reclaim_after_cost_bps": 0.0,
                "min_low_age_seconds": 5.0,
                "max_low_age_seconds": 0.0,
                "min_reclaim_velocity_bps_per_second": 0.0,
            },
        )()
        trigger = {"dump_bps": -100.0, "reclaim_bps": 100.0, "low_bid": 99.0, "low_at_epoch": tape.time.time()}
        pair = type("Pair", (), {"cost_min": 1.0, "order_min": 0.0001})()
        book = tape.Book(
            bid=100.0,
            ask=100.1,
            bids=[tape.Level(price=100.0, size=10.0)],
            asks=[tape.Level(price=100.1, size=10.0)],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            position = tape.open_position_from_trigger(
                product_id="TEST-USD",
                pair=pair,
                book=book,
                trigger=trigger,
                args=args,
                event_path=Path(tmpdir) / "events.jsonl",
            )

        self.assertIsNone(position)

    def test_open_position_blocks_slow_reclaim_velocity(self) -> None:
        args = type(
            "Args",
            (),
            {
                "entry_trigger_mode": "reclaim",
                "deploy_usd": 15.0,
                "taker_fee_bps": 40.0,
                "max_spread_bps": 500.0,
                "min_reclaim_after_spread_bps": 0.0,
                "min_reclaim_after_cost_bps": 0.0,
                "min_low_age_seconds": 0.0,
                "max_low_age_seconds": 0.0,
                "min_reclaim_velocity_bps_per_second": 10.0,
            },
        )()
        trigger = {"dump_bps": -100.0, "reclaim_bps": 20.0, "low_bid": 99.0, "low_at_epoch": tape.time.time() - 10.0}
        pair = type("Pair", (), {"cost_min": 1.0, "order_min": 0.0001})()
        book = tape.Book(
            bid=100.0,
            ask=100.1,
            bids=[tape.Level(price=100.0, size=10.0)],
            asks=[tape.Level(price=100.1, size=10.0)],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            position = tape.open_position_from_trigger(
                product_id="TEST-USD",
                pair=pair,
                book=book,
                trigger=trigger,
                args=args,
                event_path=Path(tmpdir) / "events.jsonl",
            )

        self.assertIsNone(position)

    def test_open_position_blocks_reclaim_that_does_not_clear_fee_adjusted_cost(self) -> None:
        args = type(
            "Args",
            (),
            {
                "entry_trigger_mode": "reclaim",
                "deploy_usd": 15.0,
                "taker_fee_bps": 40.0,
                "max_spread_bps": 500.0,
                "min_reclaim_after_spread_bps": 0.0,
                "min_reclaim_after_cost_bps": 25.0,
                "min_low_age_seconds": 0.0,
                "max_low_age_seconds": 0.0,
                "min_reclaim_velocity_bps_per_second": 0.0,
            },
        )()
        trigger = {"dump_bps": -100.0, "reclaim_bps": 60.0, "low_bid": 99.0, "low_at_epoch": tape.time.time() - 5.0}
        pair = type("Pair", (), {"cost_min": 1.0, "order_min": 0.0001})()
        book = tape.Book(
            bid=100.0,
            ask=100.1,
            bids=[tape.Level(price=100.0, size=10.0)],
            asks=[tape.Level(price=100.1, size=10.0)],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            position = tape.open_position_from_trigger(
                product_id="TEST-USD",
                pair=pair,
                book=book,
                trigger=trigger,
                args=args,
                event_path=Path(tmpdir) / "events.jsonl",
            )

        self.assertIsNone(position)


if __name__ == "__main__":
    unittest.main()
