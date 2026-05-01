import argparse
import unittest

from build_kraken_spot_velocity_loss_autopsy import guard_blockers, pair_trades


class KrakenSpotVelocityLossAutopsyTests(unittest.TestCase):
    def test_pair_trades_uses_latest_matching_open(self) -> None:
        events = [
            {"event": "shadow_open", "at": "2026-04-24T00:00:00+00:00", "product_id": "A-USD", "row": {"id": "old"}},
            {"event": "shadow_open", "at": "2026-04-24T00:01:00+00:00", "product_id": "A-USD", "row": {"id": "latest"}},
            {"event": "shadow_close", "at": "2026-04-24T00:02:00+00:00", "product_id": "A-USD", "net_pnl": -1},
        ]
        trades, anomalies = pair_trades(events)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["open"]["row"]["id"], "latest")
        self.assertEqual(len(anomalies), 1)

    def test_guard_blockers_flags_chase_and_stale_window(self) -> None:
        args = argparse.Namespace(
            min_kraken_edge_bps=50.0,
            max_spread_bps=100.0,
            allowed_signal_states="live_hot,building",
            allowed_best_windows="last,30s,60s,5m",
            required_verdicts="clears_both_fee_models,kraken_fee_flip_candidate",
            max_entry_chase_bps=450.0,
        )
        row = {
            "can_trade_starting_cash": True,
            "signal_state": "live_hot",
            "best_move_window": "15m",
            "best_move_bps": 600,
            "spread_bps": 20,
            "kraken_edge_bps": 300,
            "verdict": "clears_both_fee_models",
        }
        blockers = guard_blockers(row, args)
        self.assertEqual(blockers, ["best_window_not_allowed", "entry_chase_too_large"])


if __name__ == "__main__":
    unittest.main()
