import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_kraken_spot_dislocation_reversion_lab import (  # noqa: E402
    Setup,
    evaluate_long_entry,
    feature_row,
    rank_value,
    setup_allows,
)


class KrakenSpotDislocationReversionLabTests(unittest.TestCase):
    def test_feature_row_detects_downward_dislocation(self) -> None:
        samples = [
            {"ts": 0, "bid": 100.0, "ask": 100.2},
            {"ts": 60, "bid": 98.0, "ask": 98.1},
        ]
        setup = Setup("test", 60, 50.0, 50.0, 50.0)
        row = feature_row(samples, 1, setup)
        self.assertGreater(row["dislocation_bps"], 190.0)
        self.assertGreater(row["ask_discount_bps"], 190.0)

    def test_setup_allows_blocks_expensive_spread(self) -> None:
        row = {
            "dislocation_bps": 100.0,
            "spread_bps": 200.0,
            "ask_discount_bps": 100.0,
            "spread_expansion_bps": 0.0,
            "context_move_bps": 0.0,
        }
        allowed, _opportunity = setup_allows(row, Setup("test", 60, 50.0, 100.0, 10.0))
        self.assertFalse(allowed)

    def test_setup_allows_spread_washout_requires_expansion(self) -> None:
        row = {
            "dislocation_bps": 100.0,
            "spread_bps": 50.0,
            "ask_discount_bps": 100.0,
            "spread_expansion_bps": 5.0,
            "context_move_bps": 0.0,
        }
        allowed, _opportunity = setup_allows(row, Setup("test", 60, 50.0, 100.0, 10.0, min_spread_expansion_bps=25.0))
        self.assertFalse(allowed)

    def test_evaluate_long_entry_records_mfe_and_target_hit(self) -> None:
        samples = [
            {"ts": 0, "bid": 99.9, "ask": 100.0},
            {"ts": 30, "bid": 101.2, "ask": 101.3},
            {"ts": 60, "bid": 100.5, "ask": 100.6},
        ]
        marks = evaluate_long_entry(
            samples,
            0,
            deploy_usd=80.0,
            execution_model="taker",
            maker_fee_bps=25.0,
            taker_fee_bps=40.0,
            profit_buffer_bps=25.0,
            horizons=[60],
        )
        self.assertGreater(marks["60"]["mfe_net_pct"], 0)
        self.assertTrue(marks["60"]["target_hit"])

    def test_evaluate_long_entry_maker_upper_uses_bid_entry_and_ask_exit(self) -> None:
        samples = [
            {"ts": 0, "bid": 99.0, "ask": 100.0},
            {"ts": 60, "bid": 99.0, "ask": 100.0},
        ]
        taker = evaluate_long_entry(
            samples,
            0,
            deploy_usd=80.0,
            execution_model="taker",
            maker_fee_bps=25.0,
            taker_fee_bps=40.0,
            profit_buffer_bps=25.0,
            horizons=[60],
        )
        maker_upper = evaluate_long_entry(
            samples,
            0,
            deploy_usd=80.0,
            execution_model="maker-upper",
            maker_fee_bps=25.0,
            taker_fee_bps=40.0,
            profit_buffer_bps=25.0,
            horizons=[60],
        )
        self.assertGreater(maker_upper["60"]["net_pnl"], taker["60"]["net_pnl"])
        self.assertEqual(maker_upper["60"]["exit_price_field"], "ask")

    def test_rank_value_sinks_unmarked_rows(self) -> None:
        summary = {"horizons": {"300": {"marked": 0, "avg_net_pnl": 0.0}}}
        self.assertLess(rank_value(summary, "300"), -1000.0)


if __name__ == "__main__":
    unittest.main()
