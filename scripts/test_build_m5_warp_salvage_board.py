#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_m5_warp_salvage_board as board


class BuildM5WarpSalvageBoardTests(unittest.TestCase):
    def test_build_payload_marks_live_m5_restore_as_bad_idea(self) -> None:
        config_payload = {
            "lanes": [
                {"lane": "live_btcusd_m5_warp", "symbol": "BTCUSD", "step": 100, "alpha": 1.0, "max_open_per_side": 12, "realized_closes": 62, "realized_net_usd": -2288.19, "avg_per_close": -36.90, "open_positions": 6, "total_resets": 0, "reset_rate": 0.0},
                {"lane": "live_ethusd_m5_warp", "symbol": "ETHUSD", "step": 5, "alpha": 1.0, "max_open_per_side": 12, "realized_closes": 12, "realized_net_usd": -110.54, "avg_per_close": -9.21, "open_positions": 0, "total_resets": 20, "reset_rate": 1.67},
                {"lane": "shadow_btcusd_m5_warp_step200", "symbol": "BTCUSD", "step": 200, "alpha": 1.0, "max_open_per_side": 60, "realized_closes": 2, "realized_net_usd": 139.96, "avg_per_close": 69.98, "open_positions": 2, "total_resets": 0, "reset_rate": 0.0},
                {"lane": "shadow_ethusd_m5_warp_5", "symbol": "ETHUSD", "step": 5, "alpha": 1.0, "max_open_per_side": 12, "realized_closes": 20, "realized_net_usd": 157.17, "avg_per_close": 7.86, "open_positions": 2, "total_resets": 23, "reset_rate": 1.15},
                {"lane": "live_btcusd_m15_warp", "symbol": "BTCUSD", "step": 75, "alpha": 1.0, "max_open_per_side": 60, "realized_closes": 276, "realized_net_usd": 1266.74, "avg_per_close": 4.59, "open_positions": 0, "total_resets": 80, "reset_rate": 0.29},
            ]
        }
        out_payload = {
            "aggregate": {
                "train_shapeshifter_total": 616.11,
                "train_static_total": 148.38,
                "test_shapeshifter_total": 577.71,
                "test_static_total": 408.69,
                "overall_degradation": 0.9377,
                "symbols_beating_static": 4,
            }
        }

        payload = board.build_payload(config_payload, out_payload)

        rows = {row["lane"]: row for row in payload["lanes"]}
        self.assertEqual(rows["live_btcusd_m5_warp"]["verdict"], "do_not_restore_as_was")
        self.assertEqual(rows["shadow_btcusd_m5_warp_step200"]["verdict"], "salvage_probe_candidate")
        self.assertEqual(rows["shadow_ethusd_m5_warp_5"]["verdict"], "strong_salvage_candidate")
        self.assertEqual(payload["aggregate_shapeshifter_validation"]["symbols_beating_static"], 4)
        self.assertEqual(payload["ranked_next_steps"][0]["action"], "launch_btc_m5_step200_hungry_hippo_shadow_probe")

    def test_render_markdown_mentions_session_gate_policy(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "aggregate_shapeshifter_validation": {
                "test_shapeshifter_total": 10.0,
                "test_static_total": 8.0,
                "overall_degradation": 0.9,
                "symbols_beating_static": 4,
            },
            "leadership_read": ["one"],
            "lanes": [
                {"lane": "live_btcusd_m5_warp", "step": 100.0, "alpha": 1.0, "realized_closes": 1, "realized_net_usd": -1.0, "avg_per_close": -1.0, "open_positions": 0, "total_resets": 0, "verdict": "do_not_restore_as_was", "next_action": "x", "thesis": "y"}
            ],
            "universal_control_thesis": {
                "session_gate_policy": "not_primary_optimizer",
                "controller_goal": "goal",
                "core_rules": ["rule one"],
            },
            "ranked_next_steps": [{"priority": 1, "action": "a", "why": "b"}],
        }

        markdown = board.render_markdown(payload)

        self.assertIn("M5 Warp Salvage Board", markdown)
        self.assertIn("Session gate policy", markdown)
        self.assertIn("not_primary_optimizer", markdown)
        self.assertIn("1. `a`", markdown)


if __name__ == "__main__":
    unittest.main()
