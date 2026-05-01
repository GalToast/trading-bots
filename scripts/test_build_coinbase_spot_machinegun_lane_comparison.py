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

from build_coinbase_spot_machinegun_lane_comparison import summarize_capture


class CoinbaseMachinegunLaneComparisonTests(unittest.TestCase):
    def test_summarize_capture_reports_break_even_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            rows = [
                {
                    "action": "close_machinegun_shadow",
                    "gross_mfe_capture_pct": 50.0,
                    "net_mfe_capture_pct": 25.0,
                    "max_net_pct_on_cost": 2.0,
                    "net_pct_on_cost": 0.5,
                    "net_pnl": 0.2,
                },
                {
                    "action": "close_machinegun_shadow",
                    "gross_mfe_capture_pct": 10.0,
                    "net_mfe_capture_pct": 5.0,
                    "max_net_pct_on_cost": 1.0,
                    "net_pct_on_cost": -0.2,
                    "net_pnl": -0.1,
                },
            ]
            event_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            summary = summarize_capture(event_path, "close_machinegun_shadow")

            self.assertEqual(summary["close_count"], 2)
            self.assertEqual(summary["mfe_capture_count"], 2)
            self.assertEqual(summary["avg_net_mfe_capture_pct"], 15.0)
            self.assertEqual(summary["net_capture_ge_20_rate_pct"], 50.0)
            self.assertEqual(summary["verdict"], "coinbase_capture_watch_zone")


if __name__ == "__main__":
    unittest.main()
