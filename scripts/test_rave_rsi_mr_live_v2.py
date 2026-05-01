#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import rave_rsi_mr_live_v2 as mod


class RaveRsiMrLiveV2Tests(unittest.TestCase):
    def test_open_event_includes_phase_and_signal_telemetry(self) -> None:
        engine = mod.RaveRsiMrLive(starting_cash=48.0)
        engine.history = [1.0, 0.9, 0.8, 0.7]
        engine.last_candle_time = 1776000000
        candle = {
            "start": 1776000300,
            "open": 0.69,
            "high": 0.7,
            "low": 0.68,
            "close": 0.69,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine.process_candle(candle, 0.0, event_path, phase="live_forward")
            rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        event = rows[0]
        self.assertEqual(event["phase"], "live_forward")
        self.assertEqual(event["signal_bar_start"], 1776000000)
        self.assertEqual(event["signal_price"], 0.7)
        self.assertIn("signal_to_entry_gap_bps", event)
        self.assertEqual(engine.execution_phase_counts["live_forward"], 1)


if __name__ == "__main__":
    unittest.main()
