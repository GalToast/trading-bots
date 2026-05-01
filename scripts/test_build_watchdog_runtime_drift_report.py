#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_watchdog_runtime_drift_report as drift_report


class BuildWatchdogRuntimeDriftReportTests(unittest.TestCase):
    def test_build_report_flags_missing_and_extra_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "watchdog_groups.json"
            reports_dir = root / "watchdog"
            reports_dir.mkdir(parents=True, exist_ok=True)

            config_path.write_text(
                json.dumps(
                    {
                        "groups": {
                            "alpha": {"label": "Alpha", "lanes": ["lane_a", "lane_b"]},
                            "beta": {"label": "Beta", "lanes": ["lane_c"]},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (reports_dir / "alpha_loop_state.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "updated_at": "2026-04-15T19:00:00+00:00",
                        "interval_seconds": 30,
                        "lanes": ["lane_a", "lane_extra"],
                    }
                ),
                encoding="utf-8",
            )

            payload = drift_report.build_report(
                config_path=config_path,
                reports_dir=reports_dir,
                now=datetime(2026, 4, 15, 19, 1, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(payload["status"], "drift_detected")
        self.assertEqual(payload["group_count"], 2)
        self.assertEqual(payload["aligned_group_count"], 0)
        self.assertEqual(payload["drift_group_count"], 2)
        self.assertEqual(payload["missing_loop_state_groups"], ["beta"])
        alpha = next(row for row in payload["groups"] if row["group"] == "alpha")
        beta = next(row for row in payload["groups"] if row["group"] == "beta")
        self.assertEqual(alpha["missing_lanes"], ["lane_b"])
        self.assertEqual(alpha["extra_lanes"], ["lane_extra"])
        self.assertTrue(beta["drift"])
        self.assertEqual(beta["loop_status"], "missing_loop_state")

    def test_build_report_marks_stale_loop_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "watchdog_groups.json"
            reports_dir = root / "watchdog"
            reports_dir.mkdir(parents=True, exist_ok=True)

            config_path.write_text(
                json.dumps({"groups": {"alpha": {"label": "Alpha", "lanes": ["lane_a"]}}}),
                encoding="utf-8",
            )
            (reports_dir / "alpha_loop_state.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "updated_at": "2026-04-15T19:00:00+00:00",
                        "interval_seconds": 30,
                        "lanes": ["lane_a"],
                    }
                ),
                encoding="utf-8",
            )

            payload = drift_report.build_report(
                config_path=config_path,
                reports_dir=reports_dir,
                now=datetime(2026, 4, 15, 19, 3, 5, tzinfo=timezone.utc),
            )

        self.assertEqual(payload["status"], "drift_detected")
        alpha = payload["groups"][0]
        self.assertTrue(alpha["loop_state_stale"])
        self.assertEqual(alpha["loop_status"], "stale_ok")
        self.assertEqual(alpha["verdict"], "drift")

    def test_build_report_downgrades_stale_empty_group_to_retired_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "watchdog_groups.json"
            reports_dir = root / "watchdog"
            reports_dir.mkdir(parents=True, exist_ok=True)

            config_path.write_text(
                json.dumps({"groups": {"retired_alpha": {"label": "Retired Alpha", "lanes": []}}}),
                encoding="utf-8",
            )
            (reports_dir / "retired_alpha_loop_state.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "updated_at": "2026-04-15T19:00:00+00:00",
                        "interval_seconds": 30,
                        "lanes": ["old_lane_a"],
                    }
                ),
                encoding="utf-8",
            )

            payload = drift_report.build_report(
                config_path=config_path,
                reports_dir=reports_dir,
                now=datetime(2026, 4, 15, 19, 3, 5, tzinfo=timezone.utc),
            )

        self.assertEqual(payload["status"], "ok_with_retired_residue")
        self.assertEqual(payload["aligned_group_count"], 0)
        self.assertEqual(payload["drift_group_count"], 0)
        self.assertEqual(payload["retired_residue_group_count"], 1)
        self.assertEqual(payload["retired_residue_groups"], ["retired_alpha"])
        self.assertEqual(payload["missing_loop_state_group_count"], 0)
        retired_alpha = payload["groups"][0]
        self.assertFalse(retired_alpha["drift"])
        self.assertTrue(retired_alpha["retired_residue"])
        self.assertEqual(retired_alpha["verdict"], "retired_residue")
        self.assertEqual(retired_alpha["loop_status"], "retired_stale_ok")

    def test_render_markdown_includes_operator_guidance(self) -> None:
        text = drift_report.render_markdown(
            {
                "generated_at": "2026-04-15T19:05:00+00:00",
                "status": "drift_detected",
                "group_count": 2,
                "aligned_group_count": 1,
                "drift_group_count": 1,
                "retired_residue_group_count": 0,
                "missing_loop_state_group_count": 0,
                "groups": [
                    {
                        "group": "crypto_watchdog",
                        "verdict": "aligned",
                        "loop_status": "ok",
                        "configured_lane_count": 14,
                        "running_lane_count": 14,
                        "missing_lanes": [],
                        "extra_lanes": [],
                    },
                    {
                        "group": "fx_watchdog",
                        "verdict": "drift",
                        "loop_status": "ok",
                        "configured_lane_count": 11,
                        "running_lane_count": 6,
                        "missing_lanes": ["shadow_gbpjpy_m15_warp"],
                        "extra_lanes": [],
                    },
                ],
            }
        )

        self.assertIn("# Watchdog Runtime Drift", text)
        self.assertIn("relaunch only the affected watchdog group wrapper", text)
        self.assertIn("retired residue", text)
        self.assertIn("fx_watchdog", text)
        self.assertIn("shadow_gbpjpy_m15_warp", text)


if __name__ == "__main__":
    unittest.main()
