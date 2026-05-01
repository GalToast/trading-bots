#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import monitor_experimental_lanes as monitor


class _OffSessionDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls(2026, 4, 16, 2, 45, tzinfo=timezone.utc if tz else None)


class MonitorExperimentalLanesTests(unittest.TestCase):
    def test_extract_lane_metrics_supports_nested_symbol_payloads(self) -> None:
        payload = {
            "symbols": {
                "ETHUSD": {
                    "open_tickets": [{"pnl_usd": 1.25}, {"profit_usd": -0.75}],
                    "realized_closes": 12,
                    "realized_net_usd": -158.28,
                    "anchor_resets": 2,
                }
            }
        }

        metrics = monitor.extract_lane_metrics(payload)

        self.assertEqual(metrics["closes"], 12)
        self.assertEqual(metrics["opens"], 2)
        self.assertEqual(metrics["net"], -158.28)
        self.assertEqual(metrics["resets"], 2)
        self.assertAlmostEqual(metrics["floating"], 0.5)

    def test_check_lane_supports_legacy_flat_payloads(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lane.json"
            path.write_text(
                json.dumps(
                    {
                        "close_count": 4,
                        "open_positions": 3,
                        "total_realized_pnl": 18.5,
                        "reset_count": 1,
                        "floating_pnl": -2.25,
                    }
                ),
                encoding="utf-8",
            )

            result = monitor.check_lane("legacy", str(path))

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["closes"], 4)
        self.assertEqual(result["opens"], 3)
        self.assertEqual(result["net"], 18.5)
        self.assertEqual(result["resets"], 1)
        self.assertEqual(result["floating"], -2.25)
        self.assertAlmostEqual(result["per_close"], 4.625)

    def test_main_off_session_banner_is_ascii_safe(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(monitor, "LANES", {}),
            patch.object(monitor, "datetime", _OffSessionDateTime),
            redirect_stdout(stdout),
        ):
            result = monitor.main()

        output = stdout.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Experimental Lane Monitor - 2026-04-16 02:45 UTC", output)
        self.assertIn("WARNING: OFF-SESSION - lanes are idling. First closes expected at 07:00 UTC.", output)


if __name__ == "__main__":
    unittest.main()
