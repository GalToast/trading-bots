#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_fx_proof_health_board as board


class FxProofHealthBoardTests(unittest.TestCase):
    def test_build_gbp_shadow_row_surfaces_divergence(self) -> None:
        state = {
            "runner": {"heartbeat_at": "2026-04-13T19:58:49+00:00"},
            "durable_proof": {
                "counter_regressed": True,
                "durable_open_count": 20,
                "durable_realized_closes": 3,
                "durable_realized_net_usd": 0.73,
                "last_seen_at": "2026-04-13T18:29:52+00:00",
            },
            "symbols": {
                "GBPUSD": {
                    "realized_closes": 0,
                    "realized_net_usd": 0.0,
                    "open_tickets": [{"direction": "SELL"}] * 23,
                }
            },
        }
        report = "| Marked Net (USD) | $-49.31 |\n"
        with (
            patch.object(board, "load_json", return_value=state),
            patch.object(board, "load_text", return_value=report),
            patch.object(board, "age_hours_text", return_value="1.50h"),
        ):
            row = board.build_gbp_shadow_row()

        self.assertEqual(row["proof_status"], "proof_positive")
        self.assertEqual(row["close_gap"], 3)
        self.assertEqual(row["open_gap"], -3)
        self.assertTrue(row["counter_regressed"])
        self.assertIn("snapshot behind durable proof", row["note"])

    def test_build_gbp_shadow_row_marks_negative_net_as_proof_negative(self) -> None:
        state = {
            "runner": {"heartbeat_at": "2026-04-16T02:04:17+00:00"},
            "durable_proof": {
                "counter_regressed": False,
                "durable_open_count": 4,
                "durable_realized_closes": 7313,
                "durable_realized_net_usd": -1932.51,
                "last_seen_at": "2026-04-16T02:04:17+00:00",
            },
            "symbols": {
                "GBPUSD": {
                    "realized_closes": 7313,
                    "realized_net_usd": -1932.51,
                    "open_tickets": [{"direction": "SELL"}] * 4,
                }
            },
        }
        report = "| Marked Net (USD) | $-1931.63 |\n"
        with (
            patch.object(board, "load_json", return_value=state),
            patch.object(board, "load_text", return_value=report),
            patch.object(board, "age_hours_text", return_value="0.00h"),
        ):
            row = board.build_gbp_shadow_row()

        self.assertEqual(row["proof_status"], "proof_negative")
        self.assertIn("net is negative", row["note"])

    def test_build_payload_counts_divergent_lanes(self) -> None:
        with (
            patch.object(board, "build_live_reference_row", return_value={"proof_status": "graduated_live", "close_gap": 0, "open_gap": 0, "counter_regressed": False}),
            patch.object(board, "build_gbp_shadow_row", return_value={"proof_status": "proof_negative", "close_gap": 3, "open_gap": -3, "counter_regressed": True}),
            patch.object(board, "utc_now_iso", return_value="2026-04-13T20:00:00+00:00"),
        ):
            payload = board.build_payload()

        self.assertEqual(payload["summary"]["lanes"], 2)
        self.assertEqual(payload["summary"]["proof_positive_lanes"], 0)
        self.assertEqual(payload["summary"]["divergent_lanes"], 1)


if __name__ == "__main__":
    unittest.main()
