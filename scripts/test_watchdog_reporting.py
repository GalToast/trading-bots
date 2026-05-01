#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_supervisor_watchdog_board as supervisor_board
import build_supervision_finality_board as finality_board
import build_watchdog_incident_ledger as incident_ledger


class WatchdogReportingTests(unittest.TestCase):
    def test_compact_rows_collapses_duplicate_trade_firing_events(self) -> None:
        rows = [
            {
                "ts_utc": "2026-04-12T21:09:52+00:00",
                "source": "trade_firing_monitor",
                "scope": "live_fx",
                "event": "trade_firing_anomaly_recovered",
                "target": "lane_a",
                "detail": "probable_missed_open",
            },
            {
                "ts_utc": "2026-04-12T21:09:48+00:00",
                "source": "trade_firing_monitor",
                "scope": "live_fx",
                "event": "trade_firing_anomaly_recovered",
                "target": "lane_a",
                "detail": "probable_missed_open",
            },
            {
                "ts_utc": "2026-04-12T21:08:00+00:00",
                "source": "fx_watchdog",
                "scope": "fx_watchdog",
                "event": "recovered",
                "target": "fx_watchdog",
                "detail": "loop_process_missing",
            },
        ]

        compacted = incident_ledger.compact_rows(rows)

        self.assertEqual(len(compacted), 2)
        self.assertEqual(compacted[0]["ts_utc"], "2026-04-12T21:09:52+00:00")
        self.assertEqual(compacted[1]["source"], "fx_watchdog")

    def test_board_status_respects_trade_firing_signal(self) -> None:
        ok_groups = [{"status": "ok"}]

        self.assertEqual(supervisor_board.board_status(ok_groups, {"overall_status": "ok"}), "ok")
        self.assertEqual(supervisor_board.board_status(ok_groups, {"overall_status": "watch"}), "watch")
        self.assertEqual(supervisor_board.board_status(ok_groups, {"overall_status": "alert"}), "alert")
        self.assertEqual(supervisor_board.board_status([{"status": "error"}], {"overall_status": "alert"}), "degraded")

    def test_summarize_trade_firing_includes_clean_check_and_cooldowns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            board_path = tmp / "trade_firing_board.json"
            alerts_path = tmp / "trade_firing_alerts.jsonl"
            state_path = tmp / "trade_firing_alert_state.json"

            board_path.write_text(
                """
                {
                  "overall_status": "ok",
                  "generated_at": "2026-04-12T21:20:41+00:00",
                  "probable_missed_open_count": 0,
                  "suspected_missed_open_count": 0,
                  "parity_alert_count": 0,
                  "interesting_count": 1,
                  "interesting_rows": [
                    {
                      "lane": "lane_a",
                      "notes": "clean"
                    }
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            alerts_path.write_text("", encoding="utf-8")
            state_path.write_text(
                """
                {
                  "updated_at": "2026-04-12T21:20:42+00:00",
                  "last_evaluated_at": "2026-04-12T21:20:42+00:00",
                  "last_clean_check_at": "2026-04-12T21:20:42+00:00",
                  "evaluation_status": "clean",
                  "active_anomaly_count": 0,
                  "cooldown_window_seconds": 600,
                  "active_anomalies": [],
                  "cooldowns": [
                    {
                      "lane": "lane_b",
                      "alert_code": "probable_missed_open",
                      "transition": "recovered",
                      "active": false,
                      "remaining_seconds": 480,
                      "last_emitted_at": "2026-04-12T21:18:00+00:00",
                      "next_allowed_at": "2026-04-12T21:28:00+00:00"
                    }
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )

            old_board = supervisor_board.TRADE_FIRING_BOARD_JSON
            old_alerts = supervisor_board.TRADE_FIRING_ALERTS_JSONL
            old_state = supervisor_board.TRADE_FIRING_ALERT_STATE_JSON
            try:
                supervisor_board.TRADE_FIRING_BOARD_JSON = board_path
                supervisor_board.TRADE_FIRING_ALERTS_JSONL = alerts_path
                supervisor_board.TRADE_FIRING_ALERT_STATE_JSON = state_path
                summary = supervisor_board.summarize_trade_firing()
            finally:
                supervisor_board.TRADE_FIRING_BOARD_JSON = old_board
                supervisor_board.TRADE_FIRING_ALERTS_JSONL = old_alerts
                supervisor_board.TRADE_FIRING_ALERT_STATE_JSON = old_state

        self.assertEqual(summary["evaluation_status"], "clean")
        self.assertEqual(summary["last_clean_check_at"], "2026-04-12T21:20:42+00:00")
        self.assertEqual(summary["cooldown_window_seconds"], 600)
        self.assertEqual(len(summary["cooldowns"]), 1)
        self.assertEqual(summary["cooldowns"][0]["lane"], "lane_b")
        self.assertEqual(summary["cooldowns"][0]["transition"], "recovered")

    def test_summarize_trade_firing_reports_recent_noisy_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            board_path = tmp / "trade_firing_board.json"
            alerts_path = tmp / "trade_firing_alerts.jsonl"
            state_path = tmp / "trade_firing_alert_state.json"

            board_path.write_text(
                '{"overall_status":"ok","generated_at":"2026-04-12T21:20:41+00:00","interesting_count":0,"interesting_rows":[]}',
                encoding="utf-8",
            )
            alerts_path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-12T21:08:42+00:00","event_type":"trade_firing_anomaly_detected","lane":"lane_hot","execution_alert":"probable_missed_open"}',
                        '{"ts_utc":"2026-04-12T21:09:42+00:00","event_type":"trade_firing_anomaly_recovered","lane":"lane_hot","recovered_alert":"probable_missed_open"}',
                        '{"ts_utc":"2026-04-12T21:10:42+00:00","event_type":"trade_firing_anomaly_detected","lane":"lane_hot","execution_alert":"probable_missed_open"}',
                        '{"ts_utc":"2026-04-12T21:11:42+00:00","event_type":"trade_firing_anomaly_detected","lane":"lane_warm","execution_alert":"suspected_missed_open"}',
                    ]
                ),
                encoding="utf-8",
            )
            state_path.write_text(
                """
                {
                  "updated_at": "2026-04-12T21:20:42+00:00",
                  "last_evaluated_at": "2026-04-12T21:20:42+00:00",
                  "last_clean_check_at": "",
                  "evaluation_status": "anomaly_active",
                  "active_anomaly_count": 1,
                  "cooldown_window_seconds": 600,
                  "active_anomalies": [
                    {"lane": "lane_hot", "alert_code": "probable_missed_open", "severity": "critical", "watchdog_status": "ok"}
                  ],
                  "cooldowns": []
                }
                """.strip(),
                encoding="utf-8",
            )

            old_board = supervisor_board.TRADE_FIRING_BOARD_JSON
            old_alerts = supervisor_board.TRADE_FIRING_ALERTS_JSONL
            old_state = supervisor_board.TRADE_FIRING_ALERT_STATE_JSON
            try:
                supervisor_board.TRADE_FIRING_BOARD_JSON = board_path
                supervisor_board.TRADE_FIRING_ALERTS_JSONL = alerts_path
                supervisor_board.TRADE_FIRING_ALERT_STATE_JSON = state_path
                summary = supervisor_board.summarize_trade_firing()
            finally:
                supervisor_board.TRADE_FIRING_BOARD_JSON = old_board
                supervisor_board.TRADE_FIRING_ALERTS_JSONL = old_alerts
                supervisor_board.TRADE_FIRING_ALERT_STATE_JSON = old_state

        self.assertEqual(len(summary["recent_noisy_lanes"]), 2)
        self.assertEqual(summary["recent_noisy_lanes"][0]["lane"], "lane_hot")
        self.assertTrue(summary["recent_noisy_lanes"][0]["active"])
        self.assertEqual(summary["recent_noisy_lanes"][0]["detected_count"], 2)
        self.assertEqual(summary["recent_noisy_lanes"][0]["recovered_count"], 1)

    def test_build_clusters_collapses_trade_firing_episode(self) -> None:
        rows = [
            {
                "ts_utc": "2026-04-12T21:09:52+00:00",
                "source": "trade_firing_monitor",
                "scope": "trade_firing",
                "event": "trade_firing_anomaly_recovered",
                "target": "lane_a",
                "detail": "probable_missed_open",
            },
            {
                "ts_utc": "2026-04-12T21:09:42+00:00",
                "source": "trade_firing_monitor",
                "scope": "trade_firing",
                "event": "trade_firing_anomaly_recovered",
                "target": "lane_b",
                "detail": "probable_missed_open",
            },
            {
                "ts_utc": "2026-04-12T21:08:42+00:00",
                "source": "trade_firing_monitor",
                "scope": "trade_firing",
                "event": "trade_firing_anomaly_detected",
                "target": "lane_a",
                "detail": "probable_missed_open",
            },
        ]

        clusters = incident_ledger.build_clusters(rows)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["family"], "trade_firing_incident")
        self.assertEqual(clusters[0]["row_count"], 3)
        self.assertEqual(clusters[0]["target_count"], 2)

    def test_build_clusters_marks_watchdog_repair_wave_as_bootstrap_recovery(self) -> None:
        rows = [
            {
                "ts_utc": "2026-04-13T00:07:39+00:00",
                "source": "crypto_watchdog",
                "scope": "live_crypto",
                "event": "watchdog_restart",
                "target": "live_btcusd_exc2_tight_941779",
                "detail": "started_pid=12468",
            },
            {
                "ts_utc": "2026-04-13T00:07:38.7+00:00",
                "source": "crypto_watchdog",
                "scope": "live_crypto",
                "event": "watchdog_restart",
                "target": "live_btcusd_m5_warp_probation_941780",
                "detail": "started_pid=19600",
            },
        ]

        clusters = incident_ledger.build_clusters(
            rows,
            {
                "crypto_watchdog": [incident_ledger.parse_iso("2026-04-13T00:07:33.5+00:00")],
            },
        )

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["family"], "bootstrap_recovery_wave")
        self.assertEqual(clusters[0]["bootstrap_context"]["bootstrap_started_at"], "2026-04-13T00:07:33.500000+00:00")

    def test_finality_build_payload_excludes_bootstrap_clusters_from_incidents(self) -> None:
        supervisor = {
            "overall_status": "ok",
            "generated_at": "2026-04-13T01:00:00+00:00",
            "groups": [
                {"label": "Crypto", "status": "ok", "updated_age_seconds": 5.0, "stale_lanes": []},
            ],
            "trade_firing": {"overall_status": "ok"},
        }
        ledger = {
            "clusters": [
                {
                    "source": "crypto_watchdog",
                    "family": "bootstrap_recovery_wave",
                    "start_at": "2026-04-13T00:07:38.663733+00:00",
                    "end_at": "2026-04-13T00:07:39.093600+00:00",
                    "row_count": 9,
                    "target_count": 9,
                    "bootstrap_context": {"bootstrap_started_at": "2026-04-13T00:07:33.550435+00:00"},
                },
                {
                    "source": "trade_firing_monitor",
                    "family": "trade_firing_incident",
                    "start_at": "2026-04-13T00:39:14.3063497Z",
                    "end_at": "2026-04-13T00:57:34.6680600Z",
                    "row_count": 6,
                    "target_count": 2,
                },
                {
                    "source": "crypto_launcher",
                    "family": "launcher_recycle",
                    "start_at": "2026-04-13T01:18:33.1137377Z",
                    "end_at": "2026-04-13T01:18:33.1137377Z",
                    "row_count": 1,
                    "target_count": 1,
                    "bootstrap_context": {"restart_started_at": "2026-04-13T01:18:36.7438014+00:00"},
                },
            ]
        }
        execution = {"rows": []}
        trade_state = {"last_evaluated_at": "2026-04-13T01:00:00+00:00", "active_anomaly_count": 0}

        old_supervisor = finality_board.SUPERVISOR_BOARD_JSON
        old_ledger = finality_board.INCIDENT_LEDGER_JSON
        old_execution = finality_board.EXECUTION_REPORT_JSON
        old_trade_state = finality_board.TRADE_FIRING_STATE_JSON
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                supervisor_path = tmp / "supervisor.json"
                ledger_path = tmp / "ledger.json"
                execution_path = tmp / "execution.json"
                trade_state_path = tmp / "trade_state.json"
                supervisor_path.write_text(json.dumps(supervisor), encoding="utf-8")
                ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
                execution_path.write_text(json.dumps(execution), encoding="utf-8")
                trade_state_path.write_text(json.dumps(trade_state), encoding="utf-8")
                finality_board.SUPERVISOR_BOARD_JSON = supervisor_path
                finality_board.INCIDENT_LEDGER_JSON = ledger_path
                finality_board.EXECUTION_REPORT_JSON = execution_path
                finality_board.TRADE_FIRING_STATE_JSON = trade_state_path
                payload = finality_board.build_payload()
        finally:
            finality_board.SUPERVISOR_BOARD_JSON = old_supervisor
            finality_board.INCIDENT_LEDGER_JSON = old_ledger
            finality_board.EXECUTION_REPORT_JSON = old_execution
            finality_board.TRADE_FIRING_STATE_JSON = old_trade_state

        self.assertEqual(len(payload["recent_clusters"]), 1)
        self.assertEqual(payload["recent_clusters"][0]["family"], "trade_firing_incident")
        self.assertEqual(len(payload["bootstrap_clusters"]), 1)
        self.assertEqual(payload["bootstrap_clusters"][0]["family"], "bootstrap_recovery_wave")
        self.assertEqual(len(payload["maintenance_clusters"]), 1)
        self.assertEqual(payload["maintenance_clusters"][0]["family"], "launcher_recycle")

    def test_build_clusters_marks_fast_launcher_restart_as_recycle(self) -> None:
        rows = [
            {
                "ts_utc": "2026-04-13T01:18:33.1137377Z",
                "source": "crypto_launcher",
                "scope": "launcher",
                "event": "child_exited",
                "target": "45452",
                "detail": "exit_code=-1",
            },
        ]

        clusters = incident_ledger.build_clusters(
            rows,
            {
                "crypto_launcher": [incident_ledger.parse_iso("2026-04-13T01:18:36.7438014+00:00")],
            },
        )

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["family"], "launcher_recycle")
        self.assertEqual(clusters[0]["bootstrap_context"]["restart_started_at"], "2026-04-13T01:18:36.743801+00:00")

    def test_finality_self_check_flags_stale_trade_firing_state(self) -> None:
        supervisor = {
            "generated_at": "2026-04-12T21:20:42+00:00",
            "groups": [
                {"name": "crypto_watchdog", "status": "ok", "updated_age_seconds": 5.0},
            ],
        }
        trade_state = {"last_evaluated_at": "2026-04-12T20:00:00+00:00", "active_anomaly_count": 0}

        original_now = finality_board.utc_now
        try:
            finality_board.utc_now = lambda: finality_board.parse_iso("2026-04-12T21:26:42+00:00")
            result = finality_board.self_check(supervisor, trade_state)
        finally:
            finality_board.utc_now = original_now

        self.assertEqual(result["status"], "degraded")
        self.assertIn("trade_firing_state_stale", result["failures"])


if __name__ == "__main__":
    unittest.main()
