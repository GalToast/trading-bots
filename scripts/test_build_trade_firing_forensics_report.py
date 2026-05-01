from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_trade_firing_forensics_report import build_report


class BuildTradeFiringForensicsReportTests(unittest.TestCase):
    def test_classifies_transient_without_lane_event_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            alerts = root / "alerts.jsonl"
            state = root / "lane_state.json"
            event = root / "lane_events.jsonl"
            out_log = root / "watchdog" / "lane_a.out.log"
            err_log = root / "watchdog" / "lane_a.err.log"
            out_log.parent.mkdir(parents=True, exist_ok=True)

            registry.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": str(state.relative_to(root)).replace("\\", "/"),
                                "event_path": str(event.relative_to(root)).replace("\\", "/"),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            alerts.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "lane": "lane_a",
                                "event_type": "trade_firing_anomaly_detected",
                                "ts_utc": "2026-04-12T21:08:42Z",
                                "trigger_now": "BUY@1.17",
                                "trigger_age_seconds": 209.3,
                            }
                        ),
                        json.dumps(
                            {
                                "lane": "lane_a",
                                "event_type": "trade_firing_anomaly_recovered",
                                "ts_utc": "2026-04-12T21:09:42Z",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            state.write_text(
                json.dumps({"runner": {"heartbeat_at": "2026-04-12T23:52:26Z"}}),
                encoding="utf-8",
            )
            event.write_text(
                json.dumps({"action": "open_ticket", "ts_utc": "2026-04-12T20:07:52Z"}) + "\n",
                encoding="utf-8",
            )
            out_log.write_text("", encoding="utf-8")
            err_log.write_text("", encoding="utf-8")
            event_ts = 1776024472
            os.utime(event, (event_ts, event_ts))

            report = build_report(alerts_path=alerts, registry_path=registry)

        self.assertEqual(report["lane_count"], 1)
        lane = report["lanes"][0]
        self.assertEqual(lane["classification"], "transient_probable_missed_open_without_lane_event_proof")
        self.assertEqual(lane["trigger_values"], ["BUY@1.17"])
        self.assertEqual(lane["event_file"]["last_write_utc"], "2026-04-12T20:07:52Z")
        self.assertEqual(lane["state_runner_heartbeat_at"], "2026-04-12T23:52:26Z")

    def test_classifies_lane_activity_present_when_event_updates_after_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            alerts = root / "alerts.jsonl"
            state = root / "lane_state.json"
            event = root / "lane_events.jsonl"
            out_log = root / "watchdog" / "lane_b.out.log"
            err_log = root / "watchdog" / "lane_b.err.log"
            out_log.parent.mkdir(parents=True, exist_ok=True)

            registry.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_b",
                                "state_path": str(state.relative_to(root)).replace("\\", "/"),
                                "event_path": str(event.relative_to(root)).replace("\\", "/"),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            alerts.write_text(
                json.dumps(
                    {
                        "lane": "lane_b",
                        "event_type": "trade_firing_anomaly_detected",
                        "ts_utc": "2026-04-12T21:08:42Z",
                        "trigger_now": "SELL@159.34",
                        "trigger_age_seconds": 209.3,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state.write_text(json.dumps({"runner": {"heartbeat_at": "2026-04-12T23:52:26Z"}}), encoding="utf-8")
            event.write_text(
                json.dumps({"action": "open_ticket", "ts_utc": "2026-04-12T21:08:50Z"}) + "\n",
                encoding="utf-8",
            )
            out_log.write_text("runner said something\n", encoding="utf-8")
            err_log.write_text("", encoding="utf-8")
            event_ts = 1776028130
            os.utime(event, (event_ts, event_ts))

            report = build_report(alerts_path=alerts, registry_path=registry)

        lane = report["lanes"][0]
        self.assertEqual(lane["classification"], "lane_activity_present_after_alert")


if __name__ == "__main__":
    unittest.main()
