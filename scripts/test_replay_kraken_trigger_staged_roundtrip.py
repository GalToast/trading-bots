import argparse
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import replay_kraken_trigger_staged_roundtrip as replay


class TriggerStagedRoundtripReplayTests(unittest.TestCase):
    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            min_spread_bps=100.0,
            depth_notional_usd=15.0,
            entry_trigger_mode="ask_down",
            min_entry_ask_down_bps=20.0,
            min_entry_bid_up_bps=20.0,
            exit_trigger_mode="bid_up",
            min_exit_bid_up_bps=20.0,
            min_exit_ask_down_bps=20.0,
            entry_ttl_seconds=10.0,
            exit_wait_seconds=20.0,
            exit_ttl_seconds=10.0,
            maker_fee_bps=25.0,
            min_exit_net_bps=20.0,
            ghost_penalty_bps=0.0,
            max_exit_floor_above_ask_bps=100.0,
        )

    def tick(
        self,
        index: int,
        elapsed: float,
        bid: float,
        ask: float,
        *,
        spread_bps: float = 150.0,
        ask_down_bps: float = 0.0,
        bid_up_bps: float = 0.0,
    ) -> replay.ReplayTick:
        return replay.ReplayTick(
            index=index,
            ts_utc=f"2026-04-27T00:00:{index:02d}+00:00",
            elapsed_seconds=elapsed,
            bid=bid,
            ask=ask,
            bid_depth_usd=100.0,
            ask_depth_usd=100.0,
            spread_bps=spread_bps,
            ask_down_bps=ask_down_bps,
            bid_up_bps=bid_up_bps,
        )

    def test_replay_trigger_requires_full_long_only_roundtrip(self) -> None:
        args = self.args()
        ticks = [
            self.tick(0, 0.0, 0.100, 0.110, ask_down_bps=30.0),
            self.tick(1, 1.0, 0.099, 0.100),
            self.tick(2, 2.0, 0.112, 0.114, bid_up_bps=30.0),
            self.tick(3, 3.0, 0.114, 0.116),
        ]

        row = replay.replay_trigger(ticks, ticks[0], 0.10, args)

        self.assertTrue(row["entry_fill_like"])
        self.assertTrue(row["exit_fill_like"])
        self.assertTrue(row["roundtrip_success"])
        self.assertGreater(row["net_roundtrip_bps"], 20.0)

    def test_entry_only_fill_is_not_roundtrip_success(self) -> None:
        args = self.args()
        ticks = [
            self.tick(0, 0.0, 0.100, 0.110, ask_down_bps=30.0),
            self.tick(1, 1.0, 0.099, 0.100),
            self.tick(2, 2.0, 0.101, 0.111, bid_up_bps=0.0),
        ]

        row = replay.replay_trigger(ticks, ticks[0], 0.10, args)

        self.assertTrue(row["entry_fill_like"])
        self.assertFalse(row["exit_signal_found"])
        self.assertFalse(row["roundtrip_success"])


if __name__ == "__main__":
    unittest.main()
