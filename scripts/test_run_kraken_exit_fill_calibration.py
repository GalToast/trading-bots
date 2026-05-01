"""Tests for exit-fill calibration probe."""
import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

import sys
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_kraken_exit_fill_calibration import (
    ExitCalibrationResult,
    build_pair_map,
    compute_spread_bps,
    write_reports,
    utc_now_iso,
)


class ExitFillCalibrationTests(TestCase):
    def test_compute_spread_bps(self):
        self.assertAlmostEqual(compute_spread_bps(1.0, 1.01), 99.502, places=3)
        self.assertAlmostEqual(compute_spread_bps(100.0, 100.1), 9.995, places=3)
        self.assertEqual(compute_spread_bps(0, 0), 0.0)
        self.assertEqual(compute_spread_bps(-1, 1), 0.0)

    def test_build_pair_map(self):
        payload = {
            "BTRUSD": {
                "wsname": "BTR/USD",
                "altname": "BTRUSD",
                "status": "online",
                "ordermin": "190",
                "costmin": "0.5",
                "pair_decimals": "5",
                "lot_decimals": "8",
            },
            "XXBTZUSD": {
                "wsname": "XBT/USD",
                "altname": "XXBTZUSD",
                "status": "online",
                "ordermin": "0.0001",
                "costmin": "0.5",
                "pair_decimals": "1",
                "lot_decimals": "8",
            },
        }
        pm = build_pair_map(payload)
        self.assertIn("BTR-USD", pm)
        self.assertIn("BTC-USD", pm)  # XXBTZUSD maps to BTC-USD
        self.assertEqual(pm["BTR-USD"].base, "BTR")
        self.assertEqual(pm["BTR-USD"].quote, "USD")

    def test_build_pair_map_excludes_demo(self):
        payload = {
            "BTRUSDX": {
                "wsname": "BTR/USD.d",
                "altname": "BTRUSD.d",
                "status": "online",
                "ordermin": "1",
                "costmin": "0.5",
                "pair_decimals": "5",
                "lot_decimals": "8",
            },
        }
        pm = build_pair_map(payload)
        self.assertNotIn("BTR-USD", pm)

    def test_write_reports_generates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            json_path = Path(tmp) / "report.json"
            md_path = Path(tmp) / "report.md"

            events = [
                {
                    "ts_utc": utc_now_iso(),
                    "action": "exit_fill_calibration",
                    "product_id": "HOUSE-USD",
                    "exit_target_pct": 0.10,
                    "status": "filled",
                    "entry_fill_sec": 8.5,
                    "exit_fill_sec": 45.2,
                    "net_usd": 0.05,
                    "net_pct": 0.5,
                },
                {
                    "ts_utc": utc_now_iso(),
                    "action": "exit_fill_calibration",
                    "product_id": "HOUSE-USD",
                    "exit_target_pct": 0.25,
                    "status": "exit_miss",
                    "entry_fill_sec": 9.0,
                    "exit_miss_after_sec": 180.0,
                },
                {
                    "ts_utc": utc_now_iso(),
                    "action": "exit_fill_calibration_dry_run",
                    "product_id": "BTR-USD",
                    "exit_target_pct": 0.10,
                    "status": "dry_run",
                },
            ]
            for e in events:
                event_path.write_text(event_path.read_text() + json.dumps(e) + "\n" if event_path.exists() else json.dumps(e) + "\n", encoding="utf-8")

            summary = write_reports(event_path, json_path, md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertEqual(summary["calibrations"], 2)
            self.assertEqual(summary["filled"], 1)
            self.assertEqual(summary["exit_miss"], 1)
            self.assertEqual(summary["dry_runs"], 1)
            self.assertIn("HOUSE-USD", summary["by_product_target"])
            self.assertEqual(summary["by_product_target"]["HOUSE-USD"]["0.1"]["filled"], 1)
            self.assertEqual(summary["by_product_target"]["HOUSE-USD"]["0.25"]["missed"], 1)

            md_content = md_path.read_text()
            self.assertIn("HOUSE-USD", md_content)
            self.assertIn("0.1", md_content)
            self.assertIn("0.25", md_content)
