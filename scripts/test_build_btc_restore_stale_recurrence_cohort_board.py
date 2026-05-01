from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_btc_restore_stale_recurrence_cohort_board as board


class BtcRestoreStaleRecurrenceCohortBoardTests(unittest.TestCase):
    def test_build_payload_separates_quarantine_from_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            watchdog = reports / "watchdog"
            configs = root / "configs"
            watchdog.mkdir(parents=True)
            configs.mkdir(parents=True)

            (reports / "execution_monitor_report.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "lane": board.LANE,
                                "watchdog_status": "stale_recurrence",
                                "kind": "shadow_crypto",
                                "heartbeat_at": "2026-04-16T05:50:56+00:00",
                                "pre_start_state_carry_closes": 13,
                                "pre_start_state_carry_realized_usd": -230.59,
                            },
                            {
                                "lane": "shadow_btcusd_m5_warp",
                                "watchdog_status": "stale_recurrence",
                                "kind": "shadow_crypto",
                                "heartbeat_at": "2026-04-15T23:33:06+00:00",
                                "pre_start_state_carry_closes": 9,
                                "pre_start_state_carry_realized_usd": -165.0,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (watchdog / "crypto_watchdog_report.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "name": "shadow_nas100_m5_warp",
                                "enabled": True,
                                "status": "quarantined",
                                "heartbeat_at": "2026-04-16T06:15:16+00:00",
                                "source_tick_lag_seconds": 100.0,
                                "source_tick_recurrence": False,
                                "reasons": ["quarantined_until=2026-04-16T06:18:16+00:00 reason=restart_storm=4/4 within 1800s"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (watchdog / "fx_watchdog_report.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
            (watchdog / "shadow_watchdog_report.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
            (watchdog / "crypto_watchdog_loop_state.json").write_text(
                json.dumps({"lanes": ["shadow_nas100_m5_warp"], "status_counts": {"quarantined": 1}, "updated_at": "2026-04-16T06:15:33+00:00"}),
                encoding="utf-8",
            )
            (watchdog / "crypto_watchdog_quarantine_state.json").write_text(
                json.dumps(
                    {
                        "lanes": {
                            board.LANE: {
                                "kind": "shadow_crypto",
                                "quarantined_until": "2026-04-16T06:20:55+00:00",
                                "reason": "restart_storm=4/4 within 1800s",
                                "restart_count_window": 4,
                            },
                            "shadow_nas100_m5_warp": {
                                "kind": "shadow_crypto",
                                "quarantined_until": "2026-04-16T06:18:16+00:00",
                                "reason": "restart_storm=4/4 within 1800s",
                                "restart_count_window": 4,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (reports / "btc_restore_supervision_incident_board.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "overnight_action_status": "hold_runtime_repair_candidate",
                            "currently_in_crypto_watchdog_lane_set": False,
                            "quarantine_reason": "restart_storm=4/4 within 1800s",
                            "quarantined_until": "2026-04-16T06:20:55+00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (configs / "penetration_lattice_runner_registry.json").write_text(
                json.dumps(
                    {
                        "lanes": [
                            {"name": board.LANE, "enabled": False, "pause_note": "quarantined_restore"},
                            {"name": "shadow_btcusd_m5_warp", "enabled": False, "pause_note": "old_disabled"},
                            {"name": "shadow_nas100_m5_warp", "enabled": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(board, "ROOT", root), patch.object(board, "REPORTS", reports), patch.object(board, "WATCHDOG", watchdog), patch.object(board, "CONFIGS", configs), patch.object(board, "EXECUTION_PATH", reports / "execution_monitor_report.json"), patch.object(board, "CRYPTO_REPORT_PATH", watchdog / "crypto_watchdog_report.json"), patch.object(board, "FX_REPORT_PATH", watchdog / "fx_watchdog_report.json"), patch.object(board, "SHADOW_REPORT_PATH", watchdog / "shadow_watchdog_report.json"), patch.object(board, "CRYPTO_LOOP_STATE_PATH", watchdog / "crypto_watchdog_loop_state.json"), patch.object(board, "CRYPTO_QUARANTINE_PATH", watchdog / "crypto_watchdog_quarantine_state.json"), patch.object(board, "BTC_INCIDENT_PATH", reports / "btc_restore_supervision_incident_board.json"), patch.object(board, "REGISTRY_PATH", configs / "penetration_lattice_runner_registry.json"):
                payload = board.build_payload()

            self.assertEqual(payload["summary"]["crypto_quarantine_total"], 2)
            self.assertEqual(payload["summary"]["execution_stale_recurrence_total"], 2)
            self.assertEqual(payload["summary"]["execution_only_stale_residue"], 1)
            self.assertEqual(payload["crypto_quarantine_cohort"][0]["lane"], board.LANE)
            self.assertFalse(payload["crypto_quarantine_cohort"][0]["currently_in_crypto_watchdog_lane_set"])
            self.assertEqual(payload["execution_only_stale_residue"][0]["lane"], "shadow_btcusd_m5_warp")

    def test_render_markdown_mentions_sections(self) -> None:
        payload = {
            "generated_at": "2026-04-16T06:20:00+00:00",
            "leadership_read": ["restore is distinct"],
            "summary": {
                "restore_lane": board.LANE,
                "restore_incident_status": "hold_runtime_repair_candidate",
                "restore_currently_in_crypto_watchdog_lane_set": False,
                "restore_quarantine_reason": "restart_storm",
                "restore_quarantined_until": "2026-04-16T06:20:55+00:00",
                "crypto_watchdog_non_ok": 3,
                "fx_watchdog_non_ok": 0,
                "shadow_watchdog_non_ok": 1,
                "crypto_quarantine_total": 4,
                "execution_stale_recurrence_total": 11,
                "execution_only_stale_residue": 8,
            },
            "crypto_quarantine_cohort": [
                {
                    "lane": board.LANE,
                    "enabled": False,
                    "currently_in_crypto_watchdog_lane_set": False,
                    "report_status": "",
                    "reason": "restart_storm",
                    "source_tick_lag_seconds": None,
                    "source_tick_recurrence": False,
                    "registry_pause_note": "quarantined_restore",
                }
            ],
            "active_watchdog_non_ok_rows": [],
            "execution_only_stale_residue": [],
        }
        text = board.render_markdown(payload)
        self.assertIn("# BTC Restore Stale-Recurrence Cohort Board", text)
        self.assertIn("Crypto Quarantine Cohort", text)
        self.assertIn("Execution-Only Stale Residue", text)


if __name__ == "__main__":
    unittest.main()
