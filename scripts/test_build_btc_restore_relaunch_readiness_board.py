from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_btc_restore_relaunch_readiness_board as board


class BtcRestoreRelaunchReadinessBoardTests(unittest.TestCase):
    def test_payload_blocks_on_runtime_repair_after_quarantine_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            watchdog = reports / "watchdog"
            reports.mkdir(parents=True)
            watchdog.mkdir(parents=True)

            (reports / "btc_restore_supervision_incident_board.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "quarantined_until": "2026-04-16T06:20:55+00:00",
                            "registry_pause_note": "quarantined_restore",
                            "overnight_action_status": "hold_runtime_repair_candidate",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (reports / "btc_restore_stale_recurrence_cohort_board.json").write_text(
                json.dumps({"summary": {"execution_only_stale_residue": 8}}),
                encoding="utf-8",
            )
            (watchdog / "supervisor_watchdog_board.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "name": "crypto_watchdog",
                                "status": "ok",
                                "not_ok_count": 2,
                                "status_counts": {"ok": 8, "stale_recurrence": 2},
                                "updated_at": "2026-04-16T06:22:00+00:00",
                                "stale_lanes": [{"name": "shadow_nas100_m5_warp", "status": "stale_recurrence", "reasons": ["lag"]}],
                            }
                        ],
                        "trade_firing": {"overall_status": "ok", "active_anomaly_count": 0, "last_detected": {}, "last_recovered": {}},
                    }
                ),
                encoding="utf-8",
            )
            (watchdog / "crypto_watchdog_quarantine_state.json").write_text(json.dumps({"lanes": {}}), encoding="utf-8")
            (reports / "adaptive_overnight_launch_packet_board.json").write_text(
                json.dumps({"rows": [{"packet_id": board.PACKET_ID, "action_status": "hold_runtime_repair_candidate", "registry_pause_note": "quarantined_restore"}]}),
                encoding="utf-8",
            )

            fake_now = board.datetime(2026, 4, 16, 6, 22, 0, tzinfo=board.timezone.utc)

            class FrozenDateTime(board.datetime):
                @classmethod
                def now(cls, tz=None):
                    return fake_now if tz else fake_now.replace(tzinfo=None)

            with patch.object(board, "ROOT", root), patch.object(board, "REPORTS", reports), patch.object(board, "WATCHDOG", watchdog), patch.object(board, "INCIDENT_PATH", reports / "btc_restore_supervision_incident_board.json"), patch.object(board, "COHORT_PATH", reports / "btc_restore_stale_recurrence_cohort_board.json"), patch.object(board, "SUPERVISOR_PATH", watchdog / "supervisor_watchdog_board.json"), patch.object(board, "QUARANTINE_PATH", watchdog / "crypto_watchdog_quarantine_state.json"), patch.object(board, "OVERNIGHT_PACKET_PATH", reports / "adaptive_overnight_launch_packet_board.json"), patch.object(board, "datetime", FrozenDateTime):
                payload = board.build_payload()

            self.assertEqual(payload["summary"]["relaunch_gate_status"], "blocked_runtime_repair")
            self.assertFalse(payload["summary"]["incident_quarantine_active"])
            self.assertFalse(payload["summary"]["current_quarantine_contains_restore"])
            self.assertEqual(payload["summary"]["crypto_watchdog_not_ok_count"], 2)

    def test_payload_blocks_on_current_quarantine_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            watchdog = reports / "watchdog"
            reports.mkdir(parents=True)
            watchdog.mkdir(parents=True)

            (reports / "btc_restore_supervision_incident_board.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
            (reports / "btc_restore_stale_recurrence_cohort_board.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
            (watchdog / "supervisor_watchdog_board.json").write_text(json.dumps({"groups": [], "trade_firing": {}}), encoding="utf-8")
            (watchdog / "crypto_watchdog_quarantine_state.json").write_text(
                json.dumps({"lanes": {board.LANE: {"quarantined_until": "2026-04-16T06:50:49+00:00"}}}),
                encoding="utf-8",
            )
            (reports / "adaptive_overnight_launch_packet_board.json").write_text(
                json.dumps({"rows": [{"packet_id": board.PACKET_ID, "action_status": "hold_runtime_repair_candidate"}]}),
                encoding="utf-8",
            )

            fake_now = board.datetime(2026, 4, 16, 6, 22, 0, tzinfo=board.timezone.utc)

            class FrozenDateTime(board.datetime):
                @classmethod
                def now(cls, tz=None):
                    return fake_now if tz else fake_now.replace(tzinfo=None)

            with patch.object(board, "ROOT", root), patch.object(board, "REPORTS", reports), patch.object(board, "WATCHDOG", watchdog), patch.object(board, "INCIDENT_PATH", reports / "btc_restore_supervision_incident_board.json"), patch.object(board, "COHORT_PATH", reports / "btc_restore_stale_recurrence_cohort_board.json"), patch.object(board, "SUPERVISOR_PATH", watchdog / "supervisor_watchdog_board.json"), patch.object(board, "QUARANTINE_PATH", watchdog / "crypto_watchdog_quarantine_state.json"), patch.object(board, "OVERNIGHT_PACKET_PATH", reports / "adaptive_overnight_launch_packet_board.json"), patch.object(board, "datetime", FrozenDateTime):
                payload = board.build_payload()

            self.assertEqual(payload["summary"]["relaunch_gate_status"], "blocked_current_quarantine")
            self.assertTrue(payload["summary"]["current_quarantine_active"])
            self.assertTrue(payload["summary"]["current_quarantine_contains_restore"])


if __name__ == "__main__":
    unittest.main()
