import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_kraken_spot_guarded_frontier_lab import (
    Strategy,
    bps_change,
    evaluate_entry,
    feature_row,
    strategy_rank_value,
    strategy_allows,
)


class KrakenSpotGuardedFrontierLabTests(unittest.TestCase):
    def test_bps_change(self) -> None:
        self.assertAlmostEqual(bps_change(101, 100), 100.0)

    def test_feature_row_detects_live_hot(self) -> None:
        samples = [
            {"ts": 0, "bid": 100.0, "ask": 100.1},
            {"ts": 60, "bid": 101.0, "ask": 101.1},
        ]
        row = feature_row(samples, 1)
        self.assertEqual(row["signal_state"], "live_hot")
        self.assertEqual(row["best_move_window"], "last")

    def test_strategy_allows_blocks_chase(self) -> None:
        row = {"signal_state": "live_hot", "best_move_window": "last", "spread_bps": 10, "best_move_bps": 500}
        allowed, edge = strategy_allows(
            row,
            Strategy("test", 50, 100, 450, ("last",), ("live_hot",)),
            hurdle_bps=130,
        )
        self.assertFalse(allowed)
        self.assertEqual(edge, 0.0)

    def test_strategy_allows_blocks_last_tick_only_spike(self) -> None:
        row = {
            "signal_state": "live_hot",
            "best_move_window": "last",
            "spread_bps": 10,
            "best_move_bps": 90,
            "moves": {"last": 90.0, "30s": -2.0, "60s": -5.0, "5m": 0.0},
            "spread_compression_60s_bps": 0.0,
        }
        allowed, _edge = strategy_allows(
            row,
            Strategy(
                "confirmed",
                0,
                50,
                300,
                ("last", "30s", "60s"),
                ("live_hot",),
                min_last_bps=1.0,
                min_30s_bps=10.0,
                min_60s_bps=10.0,
                min_positive_short_windows=3,
                max_last_dominance_ratio=3.0,
            ),
            hurdle_bps=80,
        )
        self.assertFalse(allowed)

    def test_strategy_allows_confirmed_compressed_momentum(self) -> None:
        row = {
            "signal_state": "live_hot",
            "best_move_window": "60s",
            "spread_bps": 8,
            "best_move_bps": 160,
            "moves": {"last": 8.0, "30s": 55.0, "60s": 160.0, "5m": 40.0},
            "spread_compression_60s_bps": 4.0,
        }
        allowed, edge = strategy_allows(
            row,
            Strategy(
                "confirmed",
                25,
                20,
                300,
                ("last", "30s", "60s"),
                ("live_hot",),
                min_last_bps=1.0,
                min_30s_bps=10.0,
                min_60s_bps=20.0,
                min_positive_short_windows=3,
                max_last_dominance_ratio=3.0,
                max_spread_to_short_ratio=0.25,
                min_spread_compression_60s_bps=1.0,
            ),
            hurdle_bps=80,
        )
        self.assertTrue(allowed)
        self.assertEqual(edge, 72.0)

    def test_evaluate_entry_marks_forward_profit_after_fees(self) -> None:
        samples = [
            {"ts": 0, "bid": 100.0, "ask": 100.0},
            {"ts": 60, "bid": 102.0, "ask": 102.1},
        ]
        marks = evaluate_entry(samples, 0, deploy_usd=80.0, taker_fee_bps=40.0, horizons=[60])
        self.assertGreater(marks["60"]["net_pnl"], 0)
        self.assertGreater(marks["60"]["mfe_net_pct"], 0)

    def test_strategy_rank_value_sinks_unmarked_zero_entry_rows(self) -> None:
        summary = {"horizons": {"300": {"marked": 0, "avg_net_pnl": 0.0}}}
        self.assertLess(strategy_rank_value(summary, "300"), -1000.0)


if __name__ == "__main__":
    unittest.main()
