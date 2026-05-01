#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_kraken_spot_frontier_shadow_runner import KrakenFrontierShadowRunner
import live_kraken_spot_frontier_shadow_runner as runner_mod


class KrakenFrontierShadowRunnerTests(unittest.TestCase):
    def test_exit_profile_compresses_fixed_five_percent_target(self) -> None:
        runner = KrakenFrontierShadowRunner()
        runner.foundry_lookup = {"CQT-USD": {"atr_12_pct": 0.75}}

        profile = runner.exit_profile(
            product_id="CQT-USD",
            row={"product_id": "CQT-USD", "spread_bps": 15},
            maker=False,
            suggested_trail=0.0,
            price=0.00049,
        )

        self.assertGreaterEqual(profile["target_pct"], 0.0105)
        self.assertLess(profile["target_pct"], 0.05)
        self.assertLessEqual(profile["stop_pct"], 0.03)
        self.assertLessEqual(profile["trail_pct"], profile["target_pct"])
        self.assertEqual(profile["entry_fee_bps"], 40.0)
        self.assertEqual(profile["exit_fee_bps"], 40.0)

    def test_old_state_loads_with_new_fee_fields_defaulted(self) -> None:
        runner = KrakenFrontierShadowRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "cash": 50.0,
                        "poll_count": 7,
                        "positions": [
                            {
                                "product_id": "XCN-USD",
                                "verdict": "geometric_alpha",
                                "entry_price": 0.0048,
                                "quantity": 1000.0,
                                "cost_usd": 4.8,
                                "opened_at": "2026-04-24T00:00:00+00:00",
                                "highest_price": 0.0049,
                                "trail_pct": 0.015,
                                "target_pct": 0.05,
                                "stop_pct": 0.03,
                                "tail_prob": 0.7,
                                "fg_prob": 0.0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runner.load_state(path)

        self.assertEqual(runner.positions["XCN-USD"].entry_type, "taker")
        self.assertEqual(runner.positions["XCN-USD"].entry_fee_bps, 40.0)
        self.assertEqual(runner.positions["XCN-USD"].exit_fee_bps, 40.0)

    def test_enter_position_writes_dynamic_exit_fields(self) -> None:
        runner = KrakenFrontierShadowRunner()
        runner.cash = 100.0
        runner.foundry_lookup = {"CQT-USD": {"atr_12_pct": 0.75}}
        runner.get_fresh_tickers = lambda product_ids: {"CQTUSD": {"a": ["0.00050"], "b": ["0.00049"]}}  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            with patch.object(runner_mod, "DEFAULT_EVENT_PATH", event_path):
                runner.enter_position(
                    {"product_id": "CQT-USD", "verdict": "geometric_alpha", "spread_bps": 15, "tail_prob": 0.7},
                    maker=False,
                )
            event_written = event_path.exists()

        pos = runner.positions["CQT-USD"]
        self.assertLess(pos.target_pct, 0.05)
        self.assertGreaterEqual(pos.target_pct, 0.0105)
        self.assertEqual(pos.entry_fee_bps, 40.0)
        self.assertEqual(pos.exit_fee_bps, 40.0)
        self.assertTrue(event_written)


if __name__ == "__main__":
    unittest.main()
