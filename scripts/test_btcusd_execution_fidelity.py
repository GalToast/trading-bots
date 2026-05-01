import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("btcusd_execution_fidelity.py")
SPEC = importlib.util.spec_from_file_location("btcusd_execution_fidelity", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
fidelity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fidelity)


class BTCUSDFidelityTests(unittest.TestCase):
    def test_load_broker_total_row_returns_matching_live_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "scoreboard.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "lane_id",
                        "lane_type",
                        "symbol",
                        "updated_at",
                        "session_started_at",
                        "realized_basis",
                        "realized_usd",
                        "modeled_realized_usd",
                        "realized_gap_usd",
                        "floating_usd",
                        "net_usd",
                        "closes",
                        "open_count",
                        "avg_usd_per_close",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "lane_id": fidelity.LANE_ID,
                        "lane_type": "live",
                        "symbol": "TOTAL",
                        "updated_at": "2026-04-13T03:26:14.830766+00:00",
                        "session_started_at": "2026-04-10T21:03:10.576466+00:00",
                        "realized_basis": "broker",
                        "realized_usd": "231.26",
                        "modeled_realized_usd": "231.26",
                        "realized_gap_usd": "0.0",
                        "floating_usd": "-1105.06",
                        "net_usd": "-873.8",
                        "closes": "36",
                        "open_count": "15",
                        "avg_usd_per_close": "6.424",
                    }
                )
            original = fidelity.SCOREBOARD_PATH
            try:
                fidelity.SCOREBOARD_PATH = csv_path
                row = fidelity.load_broker_total_row()
            finally:
                fidelity.SCOREBOARD_PATH = original
        self.assertIsNotNone(row)
        self.assertEqual(row["lane_id"], fidelity.LANE_ID)
        self.assertEqual(row["symbol"], "TOTAL")
        self.assertEqual(row["realized_basis"], "broker")
        self.assertEqual(row["floating_usd"], "-1105.06")


if __name__ == "__main__":
    unittest.main()
