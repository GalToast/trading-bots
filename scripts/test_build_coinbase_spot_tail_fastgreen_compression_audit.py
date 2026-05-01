import pandas as pd
import unittest

from build_coinbase_spot_tail_fastgreen_compression_audit import compress_one_per_time, stats_for


class TailFastGreenCompressionAuditTests(unittest.TestCase):
    def test_compress_one_per_time_keeps_best_combined_prob(self) -> None:
        df = pd.DataFrame(
            [
                {"time": "2026-01-01T00:00:00Z", "tail_prob": 0.9, "fast_green_prob": 0.9, "product_id": "A"},
                {"time": "2026-01-01T00:00:00Z", "tail_prob": 0.8, "fast_green_prob": 0.99, "product_id": "B"},
                {"time": "2026-01-01T00:01:00Z", "tail_prob": 0.7, "fast_green_prob": 0.7, "product_id": "C"},
            ]
        )
        out = compress_one_per_time(df)
        self.assertEqual(len(out), 2)
        self.assertEqual(out.iloc[0]["product_id"], "A")

    def test_stats_for_empty(self) -> None:
        stat = stats_for(pd.DataFrame(), kraken_fee_bps_round_trip=80)
        self.assertEqual(stat["rows"], 0)


if __name__ == "__main__":
    unittest.main()
