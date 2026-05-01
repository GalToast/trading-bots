from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_restore_supervision_incident_board as board


class BuildBtcRestoreSupervisionIncidentBoardTests(unittest.TestCase):
    def test_build_chronology_keeps_restart_and_quarantine_events(self) -> None:
        rows = board.build_chronology(
            [
                {"lane": board.LANE, "action": "watchdog_cleanup", "ts_utc": "2026-04-16T05:48:51+00:00", "prior_reasons": ["source_tick_lag"]},
                {"lane": board.LANE, "action": "watchdog_restart", "ts_utc": "2026-04-16T05:48:52+00:00", "started_pid": 123},
                {"lane": board.LANE, "action": "watchdog_quarantine", "ts_utc": "2026-04-16T05:50:56+00:00", "reason": "restart_storm=4/4 within 1800s", "quarantined_until": "2026-04-16T06:20:55+00:00"},
                {"lane_name": board.LANE, "action": "watchdog_startup", "event": "run_watchdog_summary_exit", "status": "stale_recurrence", "ts_utc": "2026-04-16T05:49:21+00:00"},
                {"lane_name": board.LANE, "action": "watchdog_startup", "event": "run_watchdog_summary_enter", "status": "ignored", "ts_utc": "2026-04-16T05:49:20+00:00"},
            ]
        )

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["started_pid"], 123)
        self.assertEqual(rows[2]["reason"], "restart_storm=4/4 within 1800s")
        self.assertEqual(rows[3]["status"], "stale_recurrence")

    def test_build_payload_summarizes_doctrine_vs_runtime_split(self) -> None:
        overnight = {
            "rows": [
                {
                    "packet_id": "btc_restore_comparison_shadow",
                    "action_status": "hold_runtime_repair_candidate",
                    "artifact_trade_opens": 0,
                    "artifact_trade_closes": 0,
                    "first_path_verdict": "inactive_after_supervision_failure",
                }
            ]
        }
        acceptance = {
            "candidates": [
                {
                    "candidate_id": "btc_restore_comparison_shadow",
                    "verdict": "shadow_ready",
                    "queue_status": "ready",
                }
            ]
        }
        execution = {
            "rows": [
                {
                    "lane": board.LANE,
                    "pre_start_state_carry_closes": 13,
                    "pre_start_state_carry_realized_usd": -230.59,
                    "notes": "clean_forward_since_repair=+0.0000/0c, close_event_gap=13",
                }
            ]
        }
        registry = {
            "lanes": [
                {
                    "name": board.LANE,
                    "enabled": False,
                    "pause_note": "quarantined_stale_tick_recurrence_restore_packet_20260416",
                }
            ]
        }
        quarantine = {
            "lanes": {
                board.LANE: {
                    "reason": "restart_storm=4/4 within 1800s",
                    "quarantined_until": "2026-04-16T06:20:55+00:00",
                    "restart_count_window": 4,
                }
            }
        }
        loop_state = {"lanes": ["shadow_ethusd_m5_atr_optimized"], "status_counts": {"ok": 8}, "updated_at": "2026-04-16T06:04:44+00:00"}
        events = [
            {"lane": board.LANE, "action": "watchdog_cleanup", "ts_utc": "2026-04-16T05:48:51+00:00", "prior_reasons": ["source_tick_lag=10799s>120s"]},
            {"lane": board.LANE, "action": "watchdog_restart", "ts_utc": "2026-04-16T05:48:52+00:00", "started_pid": 12628},
            {"lane_name": board.LANE, "action": "watchdog_startup", "event": "run_watchdog_summary_exit", "status": "stale_recurrence", "ts_utc": "2026-04-16T05:49:21+00:00"},
            {"lane": board.LANE, "action": "watchdog_quarantine", "ts_utc": "2026-04-16T05:50:56+00:00", "reason": "restart_storm=4/4 within 1800s", "quarantined_until": "2026-04-16T06:20:55+00:00"},
        ]

        original_load_json = board.load_json
        original_load_jsonl = board.load_jsonl
        try:
            mapping = {
                board.OVERNIGHT_PACKET_PATH: overnight,
                board.ACCEPTANCE_PATH: acceptance,
                board.EXECUTION_PATH: execution,
                board.REGISTRY_PATH: registry,
                board.CRYPTO_WATCHDOG_QUARANTINE_PATH: quarantine,
                board.CRYPTO_WATCHDOG_LOOP_STATE_PATH: loop_state,
            }

            def fake_load_json(path: Path) -> dict:
                return mapping.get(path, {})

            def fake_load_jsonl(path: Path) -> list[dict]:
                if path == board.CRYPTO_WATCHDOG_EVENTS_PATH:
                    return events
                return []

            board.load_json = fake_load_json
            board.load_jsonl = fake_load_jsonl

            payload = board.build_payload()
        finally:
            board.load_json = original_load_json
            board.load_jsonl = original_load_jsonl

        summary = payload["summary"]
        self.assertEqual(summary["acceptance_verdict"], "shadow_ready")
        self.assertEqual(summary["overnight_action_status"], "hold_runtime_repair_candidate")
        self.assertFalse(summary["registry_enabled"])
        self.assertFalse(summary["currently_in_crypto_watchdog_lane_set"])
        self.assertEqual(summary["restart_count_window"], 4)
        self.assertEqual(summary["cleanup_count"], 1)
        self.assertEqual(summary["restart_count"], 1)
        self.assertEqual(summary["stale_exit_count"], 1)
        self.assertEqual(summary["quarantine_count"], 1)
        self.assertEqual(summary["first_path_verdict"], "inactive_after_supervision_failure")
        self.assertTrue(any("Doctrine still endorses" in line for line in payload["leadership_read"]))

    def test_render_markdown_mentions_quarantine_and_split(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T06:00:00+00:00",
                "summary": {
                    "lane": board.LANE,
                    "acceptance_verdict": "shadow_ready",
                    "acceptance_queue_status": "ready",
                    "overnight_action_status": "hold_runtime_repair_candidate",
                    "registry_enabled": False,
                    "registry_pause_note": "quarantined_stale_tick_recurrence_restore_packet_20260416",
                    "currently_in_crypto_watchdog_lane_set": False,
                    "quarantine_reason": "restart_storm=4/4 within 1800s",
                    "quarantined_until": "2026-04-16T06:20:55+00:00",
                    "restart_count_window": 4,
                    "cleanup_count": 4,
                    "restart_count": 4,
                    "stale_exit_count": 8,
                    "quarantine_count": 1,
                    "pre_start_state_carry_closes": 13,
                    "pre_start_state_carry_realized_usd": -230.59,
                    "current_run_trade_opens": 0,
                    "current_run_trade_closes": 0,
                    "first_path_verdict": "inactive_after_supervision_failure",
                },
                "leadership_read": ["line1"],
                "chronology": [
                    {
                        "ts_utc": "2026-04-16T05:50:56+00:00",
                        "action": "watchdog_quarantine",
                        "status": "quarantined",
                        "reason": "restart_storm=4/4 within 1800s",
                        "prior_reasons": [],
                        "started_pid": 0,
                        "quarantined_until": "2026-04-16T06:20:55+00:00",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("BTC Restore Supervision Incident Board", text)
        self.assertIn("hold_runtime_repair_candidate", text)
        self.assertIn("restart_storm=4/4 within 1800s", text)


if __name__ == "__main__":
    unittest.main()
