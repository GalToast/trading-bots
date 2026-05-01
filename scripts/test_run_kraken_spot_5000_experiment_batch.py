import pandas as pd
import unittest

from run_kraken_spot_5000_experiment_batch import dedupe, experiment_mask


class KrakenSpot5000ExperimentBatchTests(unittest.TestCase):
    def test_dedupe_keeps_first_per_product_window(self) -> None:
        df = pd.DataFrame(
            [
                {"product_id": "A", "entry_ts": 100.0},
                {"product_id": "A", "entry_ts": 120.0},
                {"product_id": "A", "entry_ts": 200.0},
            ]
        )
        out = dedupe(df, 60.0)
        self.assertEqual(out["entry_ts"].tolist(), [100.0, 200.0])

    def test_experiment_mask_dump_reclaim(self) -> None:
        features = pd.DataFrame(
            [
                {
                    "best_move_window": "60s",
                    "signal_state": "live_hot",
                    "spread_bps": 10.0,
                    "best_move_bps": 100.0,
                    "best_short_bps": 100.0,
                    "ret_5m_bps": -200.0,
                }
            ]
        )
        experiment = {
            "mode": "dump_reclaim",
            "windows": ["60s"],
            "signal_states": ["live_hot"],
            "max_spread_bps": 20,
            "max_chase_bps": 150,
            "min_dump_5m_bps": 100,
            "min_rebound_bps": 50,
            "min_edge_bps": -999,
        }
        mask = experiment_mask(features, experiment, hurdle_bps=130)
        self.assertTrue(bool(mask.iloc[0]))

    def test_experiment_mask_prebreak_compression_uses_flat_context_and_samples(self) -> None:
        features = pd.DataFrame(
            [
                {
                    "best_move_window": "30s",
                    "signal_state": "building",
                    "spread_bps": 8.0,
                    "best_move_bps": 35.0,
                    "best_short_bps": 35.0,
                    "ret_5m_bps": 12.0,
                    "ret_15m_bps": -20.0,
                    "sample_count": 100,
                    "sample_index": 25,
                },
                {
                    "best_move_window": "30s",
                    "signal_state": "building",
                    "spread_bps": 8.0,
                    "best_move_bps": 35.0,
                    "best_short_bps": 35.0,
                    "ret_5m_bps": 120.0,
                    "ret_15m_bps": -20.0,
                    "sample_count": 100,
                    "sample_index": 25,
                },
            ]
        )
        experiment = {
            "mode": "prebreak_compression",
            "windows": ["30s"],
            "signal_states": ["building"],
            "max_spread_bps": 10,
            "max_chase_bps": 50,
            "min_rebound_bps": 25,
            "max_abs_5m_bps": 25,
            "max_abs_15m_bps": 100,
            "min_samples": 60,
            "min_sample_index": 20,
            "min_edge_bps": -110,
        }
        mask = experiment_mask(features, experiment, hurdle_bps=130)
        self.assertTrue(bool(mask.iloc[0]))
        self.assertFalse(bool(mask.iloc[1]))


if __name__ == "__main__":
    unittest.main()
