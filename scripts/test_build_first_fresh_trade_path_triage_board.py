from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import build_first_fresh_trade_path_triage_board as triage


class FirstFreshTradePathTriageBoardTests(unittest.TestCase):
    def _write_events(self, path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_build_payload_distinguishes_waiting_and_first_close_verdicts(self) -> None:
        now = datetime(2026, 4, 16, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            eth_events = tmp / "eth_events.jsonl"
            shape_events = tmp / "shape_events.jsonl"

            self._write_events(
                eth_events,
                [
                    {"action": "fresh_start_prime", "ts_utc": "2026-04-16T03:47:23+00:00"},
                    {"action": "tick_history_fallback", "ts_utc": "2026-04-16T03:47:53+00:00"},
                    {
                        "action": "open_ticket",
                        "ts_utc": "2026-04-16T03:48:20+00:00",
                        "direction": "SELL",
                        "entry_context": "penetration",
                    },
                    {
                        "action": "close_ticket",
                        "ts_utc": "2026-04-16T03:49:10+00:00",
                        "direction": "SELL",
                        "realized_pnl": -1.25,
                        "time_to_first_green_seconds": 8.0,
                        "peak_pnl_before_exit": 2.4,
                    },
                ],
            )
            self._write_events(shape_events, [])

            watchdog_payload = {
                "rows": [
                    {
                        "name": "shadow_ethusd_m5_atr_optimized",
                        "status": "ok",
                        "event_path": str(eth_events),
                        "runner": {
                            "pid": 41748,
                            "started_at": "2026-04-16T03:47:23+00:00",
                            "heartbeat_at": "2026-04-16T03:59:50+00:00",
                        },
                    },
                    {
                        "name": "shadow_ethusd_m5_structure_shapeshifter",
                        "status": "ok",
                        "event_path": str(shape_events),
                        "runner": {
                            "pid": 24268,
                            "started_at": "2026-04-16T03:44:48+00:00",
                            "heartbeat_at": "2026-04-16T03:59:49+00:00",
                        },
                    },
                ]
            }
            eth_payload = {
                "active_rows": [
                    {"lane": "shadow_ethusd_m5_atr_optimized"},
                ]
            }
            shapeshifter_payload = {
                "lane_name": "shadow_ethusd_m5_structure_shapeshifter",
            }

            payload = triage.build_payload(
                now=now,
                watchdog_payload=watchdog_payload,
                eth_payload=eth_payload,
                shapeshifter_payload=shapeshifter_payload,
            )

        self.assertEqual(payload["overall_status"], "first_trade_path_available")
        rows = {row["lane"]: row for row in payload["lanes"]}
        self.assertEqual(rows["shadow_ethusd_m5_atr_optimized"]["verdict"], "went_green_failed_monetization")
        self.assertEqual(rows["shadow_ethusd_m5_structure_shapeshifter"]["verdict"], "awaiting_post_restart_runtime_event")

    def test_classifies_open_without_close_as_waiting_close(self) -> None:
        events = [
            {"action": "fresh_start_prime", "ts_utc": "2026-04-16T03:47:23+00:00"},
            {
                "action": "open_ticket",
                "ts_utc": "2026-04-16T03:48:20+00:00",
                "direction": "BUY",
                "entry_context": "breakout",
            },
        ]
        triage_payload = triage.classify_first_trade_path(events)
        self.assertEqual(triage_payload["verdict"], "first_path_opened_waiting_close")
        self.assertEqual(triage_payload["first_open_direction"], "BUY")


if __name__ == "__main__":
    unittest.main()
