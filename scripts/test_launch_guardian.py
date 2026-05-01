#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import launch_guardian as guardian


def _write_event_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class LaunchGuardianWatchdogCheckpointTests(unittest.TestCase):
    def test_select_latest_completed_cycle_prefers_previous_complete_cycle(self) -> None:
        rows = [
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:10:00+00:00",
                "action": "watchdog_startup",
                "event": "cycle_begin",
            },
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:10:00.100000+00:00",
                "event": "run_watchdog_state_loaded",
            },
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:10:00.101000+00:00",
                "event": "run_watchdog_summary_begin",
            },
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:10:00.102000+00:00",
                "event": "run_watchdog_summary_enter",
            },
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:10:00.103000+00:00",
                "event": "run_watchdog_summary_exit",
            },
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:12:00+00:00",
                "action": "watchdog_startup",
                "event": "cycle_begin",
            },
            {
                "loop_name": "fx_watchdog",
                "ts_utc": "2026-04-15T21:12:00.100000+00:00",
                "event": "run_watchdog_summary_enter",
            },
        ]

        cycle_rows = guardian.select_latest_completed_watchdog_cycle(rows, "fx_watchdog")
        summary = guardian.summarize_cycle_checkpoint_rows(cycle_rows, expected_lanes=2)

        self.assertEqual(len(cycle_rows), 4)
        self.assertEqual(summary["events"]["run_watchdog_summary_enter"], 1)
        self.assertEqual(summary["events"]["run_watchdog_summary_exit"], 1)
        self.assertFalse(summary["healthy"])

    def test_summarize_cycle_checkpoint_rows_detects_mismatch(self) -> None:
        cycle_rows = [
            {"event": "run_watchdog_state_loaded"},
            {"event": "run_watchdog_summary_begin"},
            {"event": "run_watchdog_summary_enter"},
            {"event": "run_watchdog_summary_exit"},
        ]

        summary = guardian.summarize_cycle_checkpoint_rows(cycle_rows, expected_lanes=2)
        self.assertFalse(summary["healthy"])
        self.assertEqual(summary["events"]["run_watchdog_summary_enter"], 1)
        self.assertEqual(summary["events"]["run_watchdog_summary_exit"], 1)

    def test_check_watchdog_events_health_uses_expected_lane_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            old_root = guardian.ROOT
            guardian.ROOT = tmp
            try:
                events = tmp / "reports/watchdog/fx_watchdog_events.jsonl"
                rows = [
                    {
                        "loop_name": "fx_watchdog",
                        "ts_utc": "2026-04-15T21:20:00+00:00",
                        "action": "watchdog_startup",
                        "event": "cycle_begin",
                    },
                    {
                        "loop_name": "fx_watchdog",
                        "ts_utc": "2026-04-15T21:20:00.100000+00:00",
                        "event": "run_watchdog_state_loaded",
                    },
                    {
                        "loop_name": "fx_watchdog",
                        "ts_utc": "2026-04-15T21:20:00.101000+00:00",
                        "event": "run_watchdog_summary_begin",
                    },
                    {
                        "loop_name": "fx_watchdog",
                        "ts_utc": "2026-04-15T21:20:00.102000+00:00",
                        "event": "run_watchdog_summary_enter",
                    },
                    {
                        "loop_name": "fx_watchdog",
                        "ts_utc": "2026-04-15T21:20:00.103000+00:00",
                        "event": "run_watchdog_summary_exit",
                    },
                ]
                _write_event_jsonl(events, rows)

                self.assertFalse(guardian.check_watchdog_events_health("fx_watchdog", expected_lanes=2))
            finally:
                guardian.ROOT = old_root

    def test_check_watchdog_events_health_returns_false_if_events_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            old_root = guardian.ROOT
            guardian.ROOT = tmp
            try:
                self.assertFalse(guardian.check_watchdog_events_health("missing_watchdog", expected_lanes=1))
            finally:
                guardian.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
