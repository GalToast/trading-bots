import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("btcusd_side_bias_limiter.py")
SPEC = importlib.util.spec_from_file_location("btcusd_side_bias_limiter", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
limiter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(limiter)


class BTCUSDSideBiasLimiterTests(unittest.TestCase):
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
                        "lane_id": limiter.LANE_ID,
                        "lane_type": "live",
                        "symbol": "TOTAL",
                        "updated_at": "2026-04-13T03:33:40.032663+00:00",
                        "session_started_at": "2026-04-10T21:03:10.576466+00:00",
                        "realized_basis": "broker",
                        "realized_usd": "231.26",
                        "modeled_realized_usd": "231.26",
                        "realized_gap_usd": "0.0",
                        "floating_usd": "-1099.37",
                        "net_usd": "-868.11",
                        "closes": "36",
                        "open_count": "15",
                        "avg_usd_per_close": "6.424",
                    }
                )
            original = limiter.SCOREBOARD_PATH
            try:
                limiter.SCOREBOARD_PATH = csv_path
                row = limiter.load_broker_total_row()
            finally:
                limiter.SCOREBOARD_PATH = original
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["lane_id"], limiter.LANE_ID)
        self.assertEqual(row["symbol"], "TOTAL")
        self.assertEqual(row["realized_basis"], "broker")

    def test_compare_replay_to_broker_flags_material_divergence(self) -> None:
        replay = {
            "total_realized": 754.49,
            "total_closes": 52,
            "final_open_count": 23,
        }
        broker_row = {
            "realized_usd": "231.26",
            "closes": "36",
            "open_count": "15",
        }
        comparison = limiter.compare_replay_to_broker(replay, broker_row)
        self.assertTrue(comparison["available"])
        self.assertTrue(comparison["material_divergence"])
        self.assertAlmostEqual(comparison["realized_delta"], 523.23, places=2)
        self.assertEqual(comparison["closes_delta"], 16)
        self.assertEqual(comparison["open_count_delta"], 8)


if __name__ == "__main__":
    unittest.main()
