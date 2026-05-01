from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_overnight_launch_packet_board as board


class BuildAdaptiveOvernightLaunchPacketBoardTests(unittest.TestCase):
    def test_first_path_triage_aggregates_burst_counts_before_first_close(self) -> None:
        triage = board.first_path_triage(
            [
                {
                    "action": "open_ticket",
                    "ts_utc": "2026-04-16T05:44:48.512189+00:00",
                    "direction": "BUY",
                    "entry_context": "main|off_session|wide_spread",
                    "regime_at_entry": "thin_off_session",
                    "same_bar_open_burst_count": 1,
                    "same_tick_open_burst_count": 1,
                },
                {
                    "action": "open_ticket",
                    "ts_utc": "2026-04-16T05:44:48.516083+00:00",
                    "direction": "BUY",
                    "entry_context": "main|off_session|wide_spread",
                    "regime_at_entry": "burst_expansion",
                    "same_bar_open_burst_count": 12,
                    "same_tick_open_burst_count": 12,
                },
                {
                    "action": "forced_unwind",
                    "ts_utc": "2026-04-16T05:44:48.518832+00:00",
                    "realized_pnl": -17.72,
                    "first_green_before_fail": False,
                },
            ]
        )

        self.assertEqual(triage["verdict"], "never_green_toxic_continuation")
        self.assertEqual(triage["first_open_same_bar_open_burst_count"], 12)
        self.assertEqual(triage["first_open_same_tick_open_burst_count"], 12)

    def test_guarded_admission_triage_tracks_current_run_and_first_path_counts(self) -> None:
        triage = board.guarded_admission_triage(
            [
                {
                    "action": "open_ticket",
                    "ts_utc": "2026-04-16T05:44:48.512189+00:00",
                },
                {
                    "action": "open_guarded_admission",
                    "ts_utc": "2026-04-16T05:44:48.513000+00:00",
                    "stage": "main_open",
                    "direction": "BUY",
                    "trigger_level": 101.0,
                },
                {
                    "action": "forced_unwind",
                    "ts_utc": "2026-04-16T05:44:48.518832+00:00",
                    "realized_pnl": -17.72,
                },
                {
                    "action": "open_guarded_admission",
                    "ts_utc": "2026-04-16T05:45:10.000000+00:00",
                    "stage": "rearm_open",
                    "direction": "SELL",
                    "trigger_level": 102.0,
                },
            ],
            guard_open_admission_enabled=True,
        )

        self.assertEqual(triage["status"], "observed_current_run")
        self.assertEqual(triage["current_run_event_count"], 2)
        self.assertEqual(triage["first_path_event_count"], 1)
        self.assertEqual(triage["latest_stage"], "rearm_open")
        self.assertEqual(triage["first_path_latest_stage"], "main_open")

    def test_inspect_packet_artifacts_uses_symbol_state_flag_and_separates_pre_start_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_path = tmp / "state.json"
            event_path = tmp / "events.jsonl"
            state_path.write_text(
                """
{
  "runner": {"started_at": "2026-04-16T14:34:33.549335+00:00"},
  "symbols": {
    "BTCUSD": {
      "guard_open_admission": true,
      "open_tickets": []
    }
  }
}
""".strip(),
                encoding="utf-8",
            )
            event_path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-16T14:34:20+00:00","action":"open_guarded_admission","stage":"main_open"}',
                        '{"ts_utc":"2026-04-16T14:34:40+00:00","action":"open_ticket"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            artifacts = board.inspect_packet_artifacts(str(state_path), str(event_path))

        guarded = artifacts["guarded_admission_triage"]
        self.assertTrue(artifacts["guard_open_admission_enabled"])
        self.assertEqual(guarded["status"], "pre_start_only")
        self.assertEqual(guarded["pre_start_event_count"], 1)
        self.assertEqual(guarded["current_run_event_count"], 0)


if __name__ == "__main__":
    unittest.main()
