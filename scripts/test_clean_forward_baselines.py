#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import clean_forward_baselines as baselines


class CleanForwardBaselinesTests(unittest.TestCase):
    def test_snapshot_from_symbol_state_payload_sums_symbols(self) -> None:
        payload = {
            "symbols": {
                "BTCUSD": {
                    "realized_net_usd": 12.5,
                    "realized_closes": 3,
                    "open_tickets": [{"direction": "BUY"}],
                },
                "ETHUSD": {
                    "realized_net_usd": -1.5,
                    "realized_closes": 2,
                    "open_tickets": [{"direction": "SELL"}, {"direction": "BUY"}],
                },
            }
        }

        snapshot = baselines.snapshot_from_state_payload(payload)

        self.assertEqual(snapshot["realized_net_usd"], 11.0)
        self.assertEqual(snapshot["closes"], 5)
        self.assertEqual(snapshot["open_count"], 3)
        self.assertEqual(snapshot["tracked_symbols"], 2)

    def test_record_reset_baseline_persists_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_path = tmp / "lane_state.json"
            reset_path = tmp / "clean_forward_baselines.json"
            state_path.write_text(
                """
                {
                  "engine": {
                    "realized_net_usd": 19.25,
                    "closes": 7,
                    "wins": 5,
                    "losses": 2,
                    "open_count": 1
                  },
                  "updated_at": "2026-04-12T04:30:00+00:00"
                }
                """.strip(),
                encoding="utf-8",
            )

            record = baselines.record_reset_baseline(
                lane_name="shadow_coinbase_experimental_rotation_bb_rsi",
                kind="shadow_coinbase_spot",
                state_path=state_path,
                reason="source_tick_lag=10820.6s>120.0s",
                reset_at="2026-04-12T04:31:00+00:00",
                path=reset_path,
            )

            self.assertIsNotNone(record)
            stored = baselines.load_reset_baselines(reset_path)
            self.assertEqual(stored["shadow_coinbase_experimental_rotation_bb_rsi"]["realized_net_usd"], 19.25)
            self.assertEqual(stored["shadow_coinbase_experimental_rotation_bb_rsi"]["closes"], 7)
            self.assertEqual(stored["shadow_coinbase_experimental_rotation_bb_rsi"]["reset_type"], "stale_tick_repair")

    def test_reset_baseline_for_lane_prefers_repair_snapshot(self) -> None:
        seeded = {"realized_net_usd": 5.0, "closes": 10, "seeded_at": "2026-04-12T03:00:00+00:00"}
        resets = {
            "lane_a": {
                "realized_net_usd": 2.5,
                "closes": 3,
                "reset_at": "2026-04-12T04:00:00+00:00",
                "reset_type": "stale_tick_repair",
            }
        }

        base, source = baselines.reset_baseline_for_lane("lane_a", seeded, resets)

        self.assertEqual(source, "stale_tick_repair")
        self.assertEqual(base["realized_net_usd"], 2.5)
        self.assertEqual(base["closes"], 3)


if __name__ == "__main__":
    unittest.main()
