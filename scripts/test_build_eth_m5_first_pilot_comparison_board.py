#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_eth_m5_first_pilot_comparison_board as board


class BuildEthM5FirstPilotComparisonBoardTests(unittest.TestCase):
    def test_archival_step5_mismatch_is_context_not_a_live_blocker(self) -> None:
        payload = board.build_payload(
            {
                "lanes": [
                    {"lane": "shadow_ethusd_m5_warp_5", "step": 5.0, "avg_per_close": 1.2, "realized_closes": 40, "realized_net_usd": 48.0, "total_resets": 2},
                    {"lane": "live_ethusd_m5_warp", "step": 5.0, "avg_per_close": -1.0, "realized_closes": 12, "realized_net_usd": -12.0},
                ]
            },
            {
                "probe_config": {"restart_args": ["--step", "5"], "enabled": False},
                "shadow_baseline": {},
            },
            {
                "enabled": True,
                "restart_args": ["--step", "14", "--timeframe", "M5"],
            },
            {
                "metadata": {
                    "step": 14.0,
                    "declared_step_buy_price_units": 0.14,
                    "declared_step_sell_price_units": 0.14,
                    "dynamic_geometry_enabled": False,
                },
                "runner": {"heartbeat_at": "2026-04-15T19:14:47+00:00", "pid": 29016},
                "symbols": {
                    "ETHUSD": {
                        "base_step_buy_px": 0.14,
                        "base_step_sell_px": 0.14,
                        "realized_closes": 12,
                        "realized_net_usd": -176.28,
                    }
                },
            },
            {
                "summary": {"first_pilot": "ETHUSD M5 step14 normalized control"},
                "rows": [
                    {
                        "pilot": "ETHUSD M5 step14 normalized control",
                        "status": "first_honest_pilot_after_positive_control_proof",
                        "proposed_shadow_spec": {
                            "close_scope": "",
                            "close_window": "",
                            "funding_rule": "budgeted",
                        },
                        "graduation_gate": "positive proof first",
                    }
                ],
            },
            {"ETHUSD": {"min_viable_step": 10.0, "status": "ok", "ratio": 0.1, "verdict": "pass"}},
        )

        self.assertEqual(payload["comparison_status"], "ready_for_clean_control_vs_variant")
        self.assertEqual(payload["comparison_protocol"]["blocked_by"], [])
        self.assertTrue(payload["historical_baseline"]["archival_vs_current_conflict"])

    def test_dynamic_geometry_still_blocks_comparison_hygiene(self) -> None:
        payload = board.build_payload(
            {
                "lanes": [
                    {"lane": "shadow_ethusd_m5_warp_5", "step": 5.0, "avg_per_close": 1.2, "realized_closes": 40, "realized_net_usd": 48.0, "total_resets": 2},
                    {"lane": "live_ethusd_m5_warp", "step": 5.0, "avg_per_close": -1.0, "realized_closes": 12, "realized_net_usd": -12.0},
                ]
            },
            {
                "probe_config": {"restart_args": ["--step", "5"], "enabled": False},
                "shadow_baseline": {},
            },
            {
                "enabled": True,
                "restart_args": ["--step", "14", "--timeframe", "M5"],
            },
            {
                "metadata": {
                    "step": 14.0,
                    "declared_step_buy_price_units": 0.14,
                    "declared_step_sell_price_units": 0.14,
                    "dynamic_geometry_enabled": True,
                },
                "runner": {"heartbeat_at": "2026-04-15T19:14:47+00:00", "pid": 29016},
                "symbols": {
                    "ETHUSD": {
                        "base_step_buy_px": 0.14,
                        "base_step_sell_px": 0.14,
                        "realized_closes": 12,
                        "realized_net_usd": -176.28,
                    }
                },
            },
            {
                "summary": {"first_pilot": "ETHUSD M5 step14 normalized control"},
                "rows": [
                    {
                        "pilot": "ETHUSD M5 step14 normalized control",
                        "status": "first_honest_pilot_after_positive_control_proof",
                        "proposed_shadow_spec": {
                            "close_scope": "",
                            "close_window": "",
                            "funding_rule": "budgeted",
                        },
                        "graduation_gate": "positive proof first",
                    }
                ],
            },
            {"ETHUSD": {"min_viable_step": 10.0, "status": "ok", "ratio": 0.1, "verdict": "pass"}},
        )

        self.assertEqual(payload["comparison_status"], "blocked_until_control_normalized")
        self.assertIn("dynamic geometry enabled", " ".join(payload["comparison_protocol"]["blocked_by"]))


if __name__ == "__main__":
    unittest.main()
