import argparse
import unittest

from live_kraken_spot_velocity_shadow import candidate_rows, should_exit


class KrakenSpotVelocityShadowTests(unittest.TestCase):
    def test_candidate_rows_filters_edge_and_spread(self) -> None:
        args = argparse.Namespace(
            max_spread_bps=100.0,
            min_kraken_edge_bps=0.0,
            allowed_signal_states="live_hot,building",
            allowed_best_windows="last,30s,60s,5m",
            required_verdicts="clears_both_fee_models,kraken_fee_flip_candidate",
            max_entry_chase_bps=450.0,
        )
        board = {
            "rows": [
                {
                    "product_id": "A-USD",
                    "can_trade_starting_cash": True,
                    "spread_bps": 10,
                    "kraken_edge_bps": 5,
                    "samples": 3,
                    "signal_state": "live_hot",
                    "best_move_window": "30s",
                    "best_move_bps": 40,
                    "verdict": "kraken_fee_flip_candidate",
                },
                {
                    "product_id": "B-USD",
                    "can_trade_starting_cash": True,
                    "spread_bps": 101,
                    "kraken_edge_bps": 50,
                    "samples": 3,
                    "signal_state": "live_hot",
                    "best_move_window": "30s",
                    "best_move_bps": 40,
                    "verdict": "kraken_fee_flip_candidate",
                },
                {
                    "product_id": "C-USD",
                    "can_trade_starting_cash": True,
                    "spread_bps": 10,
                    "kraken_edge_bps": -1,
                    "samples": 3,
                    "signal_state": "live_hot",
                    "best_move_window": "30s",
                    "best_move_bps": 40,
                    "verdict": "kraken_fee_flip_candidate",
                },
                {
                    "product_id": "D-USD",
                    "can_trade_starting_cash": False,
                    "spread_bps": 10,
                    "kraken_edge_bps": 5,
                    "samples": 3,
                    "signal_state": "live_hot",
                    "best_move_window": "30s",
                    "best_move_bps": 40,
                    "verdict": "kraken_fee_flip_candidate",
                },
            ]
        }
        rows = candidate_rows(board, args)
        self.assertEqual([row["product_id"] for row in rows], ["A-USD"])

    def test_should_exit_max_loss(self) -> None:
        args = argparse.Namespace(
            max_loss_pct=3.0,
            manifest_positive_within_seconds=600.0,
            manifest_positive_min_net_pct=0.0,
            min_profit_to_trail_usd=0.005,
            profit_lock_retention_pct=70.0,
        )
        exit_now, reason = should_exit({"net_pct_on_cost": -3.1, "opened_at": "2026-04-24T00:00:00+00:00"}, args)
        self.assertTrue(exit_now)
        self.assertEqual(reason, "max_loss")


if __name__ == "__main__":
    unittest.main()
