#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_starter_first_path_board as board


class BuildHungryHippoStarterFirstPathBoardTests(unittest.TestCase):
    def test_classify_first_path_green_waiting_close(self) -> None:
        verdict, rationale = board.classify_first_path(
            open_events=[{"action": "open_ticket"}],
            close_events=[],
            open_tickets=[{"first_green_seen": True}, {"first_green_seen": True}],
        )

        self.assertEqual(verdict, "opened_green_waiting_close")
        self.assertIn("gone green", rationale)

    def test_classify_first_path_green_and_monetized(self) -> None:
        verdict, rationale = board.classify_first_path(
            open_events=[{"action": "open_ticket"}],
            close_events=[{"action": "close_ticket", "realized_pnl": 1.25, "first_green_before_fail": True}],
            open_tickets=[],
        )

        self.assertEqual(verdict, "first_close_green_and_monetized")
        self.assertIn("non-negative", rationale)

    def test_build_payload_summarizes_current_starter_path(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "runtime_state": "launched_waiting_first_close",
                        "state_path": "reports/test_usdcad_state.json",
                        "event_path": "reports/test_usdcad_events.jsonl",
                    }
                ]
            },
            {
                "summary": {"starter_candidate_symbol": "USDCAD"},
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "launch_readiness": "launch_now",
                        "config_path": "configs/usdcad.json",
                        "state_path": "reports/test_usdcad_state.json",
                        "event_path": "reports/test_usdcad_events.jsonl",
                        "runtime_state": "launched_waiting_first_close",
                    }
                ],
            },
            {
                "summary": {"current_max_honest_active_lanes": 0},
                "rows": [{"current_status": "blocked_until_slot1_forward_proof", "blocker_reason": "need proof"}],
            },
        )

        self.assertEqual(payload["summary"]["starter_symbol"], "USDCAD")
        self.assertIn(payload["summary"]["starter_first_path_verdict"], {"awaiting_first_open", "opened_waiting_close", "opened_green_waiting_close", ""})

    def test_summarize_first_path_groups_same_tick_cluster_by_time_msc(self) -> None:
        summary = board.summarize_first_path(
            symbol="USDCAD",
            state_payload={
                "symbols": {
                    "USDCAD": {
                        "open_tickets": [{"first_green_seen": True} for _ in range(10)],
                    }
                }
            },
            event_rows=[
                {
                    "action": "open_ticket",
                    "ts_utc": "2026-04-16T05:09:16.218861+00:00",
                    "time_msc": 1776313457456,
                    "same_tick_open_burst_count": 3,
                    "same_bar_open_burst_count": 3,
                    "entry_context": "main|off_session|wide_spread",
                    "session_bucket": "off_session",
                    "regime_at_entry": "thin_off_session",
                    "spread_at_entry": 0.00014,
                },
                {
                    "action": "open_ticket",
                    "ts_utc": "2026-04-16T05:09:16.221411+00:00",
                    "time_msc": 1776313457456,
                    "same_tick_open_burst_count": 10,
                    "same_bar_open_burst_count": 10,
                    "entry_context": "main|off_session|wide_spread",
                    "session_bucket": "off_session",
                    "regime_at_entry": "burst_expansion",
                    "spread_at_entry": 0.00014,
                },
            ],
        )

        self.assertEqual(summary["first_cohort_open_count"], 2)
        self.assertEqual(summary["first_cohort_same_tick_burst_max"], 10)
        self.assertEqual(summary["first_cohort_opening_shape_verdict"], "burst_off_session_wide_spread")

    def test_render_markdown_mentions_verdict(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["one"],
                "summary": {
                    "starter_symbol": "USDCAD",
                    "starter_runtime_state": "launched_waiting_first_close",
                    "starter_launch_readiness": "launch_now",
                    "starter_first_path_verdict": "opened_green_waiting_close",
                    "starter_realized_closes": 0,
                    "starter_realized_net_usd": 0.0,
                    "slot1_unlock_status": "blocked_until_slot1_forward_proof",
                    "current_max_honest_active_lanes": 0,
                },
                "starter": {
                    "runtime_state": "launched_waiting_first_close",
                    "launch_readiness": "launch_now",
                    "config_path": "configs/usdcad.json",
                    "state_path": "reports/usdcad_state.json",
                    "event_path": "reports/usdcad_events.jsonl",
                    "verdict": "opened_green_waiting_close",
                    "rationale": "rationale",
                    "first_open_ts_utc": "2026-04-16T05:09:16Z",
                    "first_close_ts_utc": "",
                    "first_close_realized_pnl": 0.0,
                    "first_cohort_open_count": 10,
                    "first_cohort_same_tick_burst_max": 10,
                    "first_cohort_opening_shape_verdict": "burst_off_session_wide_spread",
                    "first_cohort_session_buckets": ["off_session"],
                    "first_cohort_entry_contexts": ["main|off_session|wide_spread"],
                    "first_cohort_regimes": ["burst_expansion"],
                    "first_cohort_max_spread_at_entry": 0.00014,
                    "current_open_count": 10,
                    "current_first_green_seen_count": 10,
                    "current_peak_pnl_before_exit_max": 0.63,
                    "current_mfe_pnl_max": 0.63,
                    "current_mae_pnl_min": -0.56,
                    "slot1_unlock_status": "blocked_until_slot1_forward_proof",
                    "slot1_blocker_reason": "need proof",
                },
                "notes": ["note"],
            }
        )

        self.assertIn("Hungry Hippo Starter First-Path Board", markdown)
        self.assertIn("opened_green_waiting_close", markdown)
        self.assertIn("burst_off_session_wide_spread", markdown)


if __name__ == "__main__":
    unittest.main()
