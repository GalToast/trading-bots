from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_trade_firing_board as board


class BuildTradeFiringBoardTests(unittest.TestCase):
    def test_build_payload_preserves_raw_alert_and_evidence_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report_path = tmp / "execution_monitor_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-12T23:59:00+00:00",
                        "rows": [
                            {
                                "lane": "lane_a",
                                "kind": "live_fx",
                                "watchdog_status": "ok",
                                "open_count": 1,
                                "close_count": 2,
                                "last_trade_event_at": "2026-04-10T20:52:05+00:00",
                                "trigger_now": "BUY@1.17",
                                "trigger_age_seconds": 220.6,
                                "execution_alert": "suspected_missed_open",
                                "raw_execution_alert": "probable_missed_open",
                                "execution_evidence_quality": "state_heartbeat_without_event_write",
                                "parity_alert": "",
                                "exact_fire_support": "full_trigger_recompute",
                                "notes": "execution_alert_downgraded=probable_missed_open->suspected_missed_open due_to=state_heartbeat_without_event_write",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            old_path = board.EXECUTION_REPORT_JSON
            try:
                board.EXECUTION_REPORT_JSON = report_path
                payload = board.build_payload()
                rendered = board.render_md(payload)
            finally:
                board.EXECUTION_REPORT_JSON = old_path

        self.assertEqual(payload["suspected_missed_open_count"], 1)
        self.assertEqual(payload["probable_missed_open_count"], 0)
        self.assertEqual(payload["interesting_rows"][0]["raw_execution_alert"], "probable_missed_open")
        self.assertEqual(payload["interesting_rows"][0]["execution_evidence_quality"], "state_heartbeat_without_event_write")
        self.assertIn("Raw Alert", rendered)
        self.assertIn("Evidence", rendered)


if __name__ == "__main__":
    unittest.main()
