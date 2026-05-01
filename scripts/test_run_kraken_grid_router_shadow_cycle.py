#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_grid_router_shadow_cycle as cycle


def args() -> argparse.Namespace:
    return argparse.Namespace(
        top_n_volume=80,
        lookback_seconds=900.0,
        trade_count=1000,
        spacing_bps=60.0,
        levels=5,
        entry_offset_mult=0.0,
        initial_capital=50.0,
        max_spread_bps=30.0,
        min_recent_trades=3,
        max_roundtrip_seconds=180.0,
        max_signal_age_seconds=90.0,
        min_depth_usd=1.0,
        trade_volume_participation=1.0,
        trade_lookback_seconds=5.0,
        shadow_duration_seconds=120.0,
        poll_seconds=2.0,
        depth_count=20,
    )


class KrakenGridRouterShadowCycleTests(unittest.TestCase):
    def test_best_fire_candidate_requires_exit_ok_and_no_blockers(self) -> None:
        payload = {
            "rows": [
                {"product_id": "AAA-USD", "roundtrip_exit_ok": True, "blockers": ["below_order_min"]},
                {"product_id": "BBB-USD", "roundtrip_exit_ok": False, "blockers": []},
                {"product_id": "CCC-USD", "roundtrip_exit_ok": True, "blockers": []},
            ]
        }

        candidate = cycle.best_fire_candidate(payload)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["product_id"], "CCC-USD")

    def test_build_shadow_command_uses_trade_tape_public_mode(self) -> None:
        cmd = cycle.build_shadow_command(
            args(),
            product_id="CC-USD",
            event_path=Path("events.jsonl"),
            summary_path=Path("summary.json"),
        )

        self.assertIn("--products", cmd)
        self.assertIn("CC-USD", cmd)
        self.assertIn("--fill-source", cmd)
        self.assertIn("trade_tape", cmd)
        self.assertIn("--duration-seconds", cmd)
        self.assertIn("120.0", cmd)

    def test_build_router_command_writes_requested_paths(self) -> None:
        cmd = cycle.build_router_command(args(), json_path=Path("router.json"), md_path=Path("router.md"))

        self.assertIn("--json-path", cmd)
        self.assertIn("router.json", cmd)
        self.assertIn("--md-path", cmd)
        self.assertIn("router.md", cmd)


if __name__ == "__main__":
    unittest.main()
