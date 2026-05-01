#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_forward_shadow_watch_board as board


class BuildHungryHippoForwardShadowWatchBoardTests(unittest.TestCase):
    def test_summarize_runtime_state_marks_not_launched_when_no_files_exist(self) -> None:
        runtime = board.summarize_runtime_state(
            symbol="USDCHF",
            config_payload={"stale_after_seconds": 240},
            state_payload=None,
            event_rows=[],
            now=datetime(2026, 4, 16, 4, 40, tzinfo=timezone.utc),
        )

        self.assertEqual(runtime["runtime_state"], "not_launched_yet")
        self.assertFalse(runtime["proof_started"])
        self.assertEqual(runtime["event_close_like_count"], 0)

    def test_summarize_runtime_state_marks_waiting_first_close_when_opens_exist(self) -> None:
        runtime = board.summarize_runtime_state(
            symbol="USDCAD",
            config_payload={"stale_after_seconds": 240},
            state_payload={
                "runner": {"heartbeat_at": "2026-04-16T04:39:00+00:00"},
                "symbols": {"USDCAD": {"open_tickets": [{"id": 1}, {"id": 2}]}},
            },
            event_rows=[{"action": "open_ticket", "ts_utc": "2026-04-16T04:38:00+00:00"}],
            now=datetime(2026, 4, 16, 4, 40, tzinfo=timezone.utc),
        )

        self.assertEqual(runtime["runtime_state"], "launched_waiting_first_close")
        self.assertEqual(runtime["current_open_count"], 2)
        self.assertEqual(runtime["event_open_count"], 1)
        self.assertFalse(runtime["proof_started"])

    def test_summarize_runtime_state_marks_forward_proof_started_on_close_like_event(self) -> None:
        runtime = board.summarize_runtime_state(
            symbol="USDCAD",
            config_payload={"stale_after_seconds": 240},
            state_payload={
                "runner": {"heartbeat_at": "2026-04-16T04:39:00+00:00"},
                "symbols": {"USDCAD": {"realized_closes": 1, "realized_net_usd": 3.25}},
            },
            event_rows=[{"action": "close_ticket", "ts_utc": "2026-04-16T04:38:30+00:00"}],
            now=datetime(2026, 4, 16, 4, 40, tzinfo=timezone.utc),
        )

        self.assertEqual(runtime["runtime_state"], "forward_proof_started")
        self.assertTrue(runtime["proof_started"])
        self.assertEqual(runtime["event_close_like_count"], 1)
        self.assertEqual(runtime["realized_closes"], 1)

    def test_render_markdown_mentions_runtime_state(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:40:00+00:00",
                "leadership_read": ["one"],
                "summary": {
                    "watch_symbol_count": 1,
                    "not_launched_symbols": ["USDCHF"],
                    "waiting_first_open_symbols": [],
                    "waiting_first_close_symbols": [],
                    "proof_started_symbols": [],
                },
                "rows": [
                    {
                        "symbol": "USDCHF",
                        "runtime_state": "not_launched_yet",
                        "config_path": "configs/hungry_hippo_usdchf_m15_extreme_shadow.json",
                        "deployment_verdict": "cleared_for_shadow_discussion",
                        "guardrail_status": "promotable_now",
                        "state_exists": False,
                        "event_exists": False,
                        "current_open_count": 0,
                        "event_open_count": 0,
                        "event_close_like_count": 0,
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "heartbeat_at": "",
                        "next_action": "wait",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Hungry Hippo Forward Shadow Watch Board", markdown)
        self.assertIn("not_launched_yet", markdown)
        self.assertIn("USDCHF", markdown)


if __name__ == "__main__":
    unittest.main()
