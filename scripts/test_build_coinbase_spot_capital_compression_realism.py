import unittest

import pandas as pd

from build_coinbase_spot_capital_compression_realism import net_pct_for_model, simulate


class CoinbaseSpotCapitalCompressionRealismTests(unittest.TestCase):
    def test_mfe_capture_subtracts_fee_and_spread(self) -> None:
        row = pd.Series({"future_mfe_pct": 4.0, "spread_bps_proxy": 20.0})
        self.assertAlmostEqual(net_pct_for_model(row, "mfe_capture", 240.0, 0.5), -0.6)

    def test_simulate_respects_one_position_capacity(self) -> None:
        signals = pd.DataFrame(
            [
                {
                    "time": 100.0,
                    "combined_score": 0.99,
                    "hold_bars": 1,
                    "product_id": "A-USD",
                    "variant_id": 1,
                    "gross_pct": 5.0,
                    "net_pct": 2.0,
                    "fee_bps_round_trip": 240.0,
                    "future_mfe_pct": 5.0,
                    "spread_bps_proxy": 0.0,
                },
                {
                    "time": 100.0,
                    "combined_score": 0.98,
                    "hold_bars": 1,
                    "product_id": "B-USD",
                    "variant_id": 1,
                    "gross_pct": 5.0,
                    "net_pct": 2.0,
                    "fee_bps_round_trip": 240.0,
                    "future_mfe_pct": 5.0,
                    "spread_bps_proxy": 0.0,
                },
            ]
        )
        result = simulate(signals, max_positions=1, deploy_pct=0.8, fee_bps=240.0, outcome_model="table_net_pct", capture_rate=1.0)
        self.assertEqual(result["trades"], 1)
        self.assertGreater(result["final_capital"], 100.0)


if __name__ == "__main__":
    unittest.main()
