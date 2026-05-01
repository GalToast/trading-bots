import argparse
import unittest

from watch_kraken_spot_guarded_candidate_forward_tape import is_recent_duplicate, make_entry, mark_entry, parse_horizons


class KrakenGuardedCandidateForwardTapeTests(unittest.TestCase):
    def test_parse_horizons_sorts_and_dedupes(self) -> None:
        self.assertEqual(parse_horizons("300,60,60,180"), [60, 180, 300])

    def test_recent_duplicate_uses_max_horizon(self) -> None:
        rows = [{"product_id": "A-USD", "entry_epoch": 100.0}]
        self.assertTrue(is_recent_duplicate(rows, "A-USD", 650.0, 60.0, [600]))
        self.assertFalse(is_recent_duplicate(rows, "A-USD", 701.0, 60.0, [600]))

    def test_mark_entry_models_bid_exit_after_fees(self) -> None:
        args = argparse.Namespace(starting_cash=100.0, deploy_pct=0.8, taker_fee_bps=40.0)
        entry = make_entry(
            {
                "product_id": "A-USD",
                "ask": 10.0,
                "bid": 9.9,
                "spread_bps": 10,
                "best_move_bps": 100,
                "best_move_window": "60s",
                "kraken_edge_bps": 50,
                "coinbase_edge_bps": -110,
                "verdict": "kraken_fee_flip_candidate",
                "signal_state": "live_hot",
            },
            args,
            [60],
            1000.0,
            "2026-04-24T00:00:00+00:00",
        )
        marked, changed = mark_entry(entry, {"bid": 10.2, "signal_state": "live_hot", "spread_bps": 5}, args, 1061.0, "now")
        self.assertTrue(changed)
        self.assertEqual(marked["status"], "complete")
        self.assertGreater(marked["marks"]["60"]["net_pnl"], 0)


if __name__ == "__main__":
    unittest.main()
