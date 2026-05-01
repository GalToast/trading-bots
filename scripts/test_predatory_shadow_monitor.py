#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from predatory_shadow_monitor import PredatoryShadowMonitor, detect_predatory_events


class PredatoryShadowMonitorTests(unittest.TestCase):
    def test_detect_predatory_events_finds_iceberg_reload(self) -> None:
        previous = {"price": 2.5, "bid": 2.49, "ask": 2.51, "bid_size": 20.0, "ask_size": 15.0, "vol_24h": 10000.0}
        current = {"price": 2.5, "bid": 2.49, "ask": 2.51, "bid_size": 25.0, "ask_size": 180.0, "vol_24h": 10000.0}
        events = detect_predatory_events("RAVE-USD", previous, current, ts_utc="2026-04-12T00:00:00Z")
        actions = [event["action"] for event in events]
        self.assertIn("iceberg_sell_reload_detected", actions)

    def test_detect_predatory_events_finds_fake_floor_pull(self) -> None:
        previous = {"price": 2.5, "bid": 2.49, "ask": 2.51, "bid_size": 1000.0, "ask_size": 20.0, "vol_24h": 100000.0}
        current = {"price": 2.49, "bid": 2.48, "ask": 2.5, "bid_size": 50.0, "ask_size": 25.0, "vol_24h": 100000.0}
        events = detect_predatory_events("RAVE-USD", previous, current, ts_utc="2026-04-12T00:00:00Z")
        actions = [event["action"] for event in events]
        self.assertIn("fake_floor_pull_detected", actions)

    def test_monitor_counts_events(self) -> None:
        monitor = PredatoryShadowMonitor(["RAVE-USD"])
        monitor.process_snapshot(
            "RAVE-USD",
            {"price": 2.53, "bid": 2.52, "ask": 2.54, "bid_size": 20.0, "ask_size": 15.0, "vol_24h": 10000.0},
            ts_utc="2026-04-12T00:00:00Z",
        )
        events = monitor.process_snapshot(
            "RAVE-USD",
            {"price": 2.53, "bid": 2.52, "ask": 2.54, "bid_size": 25.0, "ask_size": 180.0, "vol_24h": 10000.0},
            ts_utc="2026-04-12T00:00:01Z",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(monitor.event_counts["iceberg_sell_reload_detected"], 1)

    def test_detect_predatory_events_ignores_zero_price_magnetic_wall(self) -> None:
        previous = {"price": 0.0, "bid": 0.0, "ask": 0.0, "bid_size": 10.0, "ask_size": 10.0, "vol_24h": 1000.0}
        current = {"price": 0.0, "bid": 0.0, "ask": 0.0, "bid_size": 12.0, "ask_size": 11.0, "vol_24h": 1000.0}
        events = detect_predatory_events("RAVE-USD", previous, current, ts_utc="2026-04-12T00:00:00Z")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
