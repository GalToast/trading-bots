import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_fresh_start_risk_report as report


class BuildFreshStartRiskReportTest(unittest.TestCase):
    def test_classifies_open_or_historical_lanes_as_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry_path = root / "registry.json"
            groups_path = root / "groups.json"
            state_path = root / "lane_state.json"
            event_path = root / "lane_events.jsonl"

            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "shadow_test_lane",
                                "kind": "shadow_fx",
                                "enabled": True,
                                "state_path": str(state_path.relative_to(root)),
                                "event_path": str(event_path.relative_to(root)),
                                "restart_args": [
                                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                                    "--fresh-start",
                                    "--state-path",
                                    str(state_path.relative_to(root)),
                                    "--event-path",
                                    str(event_path.relative_to(root)),
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            groups_path.write_text(
                json.dumps({"groups": {"fx_watchdog": {"lanes": ["shadow_test_lane"]}}}),
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "symbols": {
                            "EURUSD": {
                                "realized_closes": 3,
                                "realized_net_usd": 12.5,
                                "open_tickets": [1],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            event_path.write_text(
                "\n".join(
                    [
                        json.dumps({"action": "fresh_start_prime", "ts_utc": "2026-04-14T19:00:00+00:00"}),
                        json.dumps({"action": "fresh_start_prime", "ts_utc": "2026-04-14T19:30:00+00:00"}),
                    ]
                ),
                encoding="utf-8",
            )

            original_root = report.ROOT
            try:
                report.ROOT = root
                rows = report.build_rows(
                    registry_path=registry_path,
                    watchdog_groups_path=groups_path,
                    now=datetime(2026, 4, 14, 19, 45, tzinfo=timezone.utc),
                )
            finally:
                report.ROOT = original_root

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["risk"], "high")
            self.assertEqual(row["close_count"], 3)
            self.assertEqual(row["open_count"], 1)
            self.assertEqual(row["fresh_start_last_60m"], 2)
            self.assertIn("fx_watchdog", row["groups"])

    def test_classifies_brand_new_fresh_start_lane_as_low_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry_path = root / "registry.json"
            groups_path = root / "groups.json"
            state_path = root / "lane_state.json"
            event_path = root / "lane_events.jsonl"

            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "shadow_new_lane",
                                "kind": "shadow_fx",
                                "enabled": True,
                                "state_path": str(state_path.relative_to(root)),
                                "event_path": str(event_path.relative_to(root)),
                                "restart_args": [
                                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                                    "--fresh-start",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            groups_path.write_text(json.dumps({"groups": {}}), encoding="utf-8")

            original_root = report.ROOT
            try:
                report.ROOT = root
                rows = report.build_rows(
                    registry_path=registry_path,
                    watchdog_groups_path=groups_path,
                    now=datetime(2026, 4, 14, 19, 45, tzinfo=timezone.utc),
                )
            finally:
                report.ROOT = original_root

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["risk"], "low")
            self.assertEqual(rows[0]["fresh_start_total"], 0)


if __name__ == "__main__":
    unittest.main()
