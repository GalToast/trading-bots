#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_eth_m15_warp_readiness as readiness


class EthM15WarpReadinessTests(unittest.TestCase):
    def test_build_payload_marks_shadow_collecting_until_50_close_gate(self) -> None:
        state = {
            "metadata": {"shared_price_max_age_ms": 1000, "step": 5.0, "raw_close_alpha": 1.0},
            "runner": {
                "heartbeat_at": "2026-04-14T04:36:38+00:00",
                "started_at": "2026-04-14T02:52:03+00:00",
                "tick_history_source_last": "shared_tick_cache",
                "latest_tick_source_last": "symbol_info_tick",
            },
            "symbols": {
                "ETHUSD": {
                    "realized_closes": 29,
                    "realized_net_usd": 557.78,
                    "anchor_resets": 0,
                    "max_open_total": 14,
                    "open_tickets": [1] * 9,
                    "raw_close_alpha": 1.0,
                    "raw_close_style": "all_profitable",
                    "momentum_gate": True,
                }
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            payload = readiness.build_payload()

        row = payload["rows"][0]
        self.assertEqual(row["lane_name"], "shadow_ethusd_m15_warp")
        self.assertEqual(row["readiness"], "shadow_collecting")
        self.assertEqual(row["gate_status"], "collecting_to_50_close_gate")
        self.assertEqual(row["progress_label"], "29/50 shadow closes")
        self.assertEqual(row["progress_pct"], "58.0%")
        self.assertEqual(row["next_gate"], "reach_50_closes_positive_reset_free")
        self.assertEqual(row["dollars_per_close"], 19.23)
        self.assertIn("economic thresholds are already clear", " ".join(payload["current_read"]).lower())

    def test_build_payload_marks_live_review_ready_after_gate_clear(self) -> None:
        state = {
            "metadata": {"shared_price_max_age_ms": 1000},
            "runner": {"heartbeat_at": "2026-04-14T05:00:00+00:00"},
            "symbols": {
                "ETHUSD": {
                    "realized_closes": 51,
                    "realized_net_usd": 1020.0,
                    "anchor_resets": 0,
                    "max_open_total": 18,
                    "open_tickets": [1] * 8,
                    "raw_close_style": "all_profitable",
                }
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            payload = readiness.build_payload()

        row = payload["rows"][0]
        self.assertEqual(row["readiness"], "live_review_ready")
        self.assertEqual(row["gate_status"], "shadow_gate_cleared")
        self.assertEqual(row["next_gate"], "manual_live_review")
        self.assertTrue(payload["summary"]["ready_for_live_review"])

    def test_build_payload_marks_failed_gate_when_closes_clear_but_economics_fail(self) -> None:
        state = {
            "metadata": {"shared_price_max_age_ms": 1000},
            "runner": {"heartbeat_at": "2026-04-15T22:20:22+00:00"},
            "symbols": {
                "ETHUSD": {
                    "realized_closes": 66,
                    "realized_net_usd": -259.57,
                    "anchor_resets": 12,
                    "max_open_total": 4,
                    "open_tickets": [],
                    "raw_close_style": "all_profitable",
                }
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            payload = readiness.build_payload()

        row = payload["rows"][0]
        self.assertEqual(row["readiness"], "shadow_gate_failed")
        self.assertEqual(row["gate_status"], "shadow_gate_failed")
        self.assertEqual(row["next_gate"], "pause_and_recover_before_new_shadow")
        self.assertEqual(
            row["gate_fail_reasons"],
            ["realized_below_bar", "dollars_per_close_below_bar", "anchor_resets_present"],
        )
        self.assertEqual(
            payload["summary"]["active_blocker"],
            "realized_below_bar,dollars_per_close_below_bar,anchor_resets_present",
        )
        self.assertIn("pause graduation", " ".join(payload["current_read"]).lower())

    def test_build_payload_prefers_per_symbol_source_fields(self) -> None:
        state = {
            "metadata": {"shared_price_max_age_ms": 1000},
            "runner": {
                "heartbeat_at": "2026-04-15T22:20:22+00:00",
                "tick_history_source_by_symbol": {
                    "ETHUSD": {
                        "last": "symbol_info_tick",
                        "counts": {"symbol_info_tick": 3},
                    }
                },
                "latest_tick_source_by_symbol": {
                    "ETHUSD": {
                        "last": "shared_tick_cache",
                        "counts": {"shared_tick_cache": 1},
                    }
                },
            },
            "symbols": {
                "ETHUSD": {
                    "realized_closes": 11,
                    "realized_net_usd": 200.0,
                    "anchor_resets": 0,
                    "max_open_total": 5,
                    "open_tickets": [],
                }
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            payload = readiness.build_payload()

        row = payload["rows"][0]
        self.assertEqual(row["tick_history_source"], "symbol_info_tick")
        self.assertEqual(row["latest_tick_source"], "shared_tick_cache")


if __name__ == "__main__":
    unittest.main()
