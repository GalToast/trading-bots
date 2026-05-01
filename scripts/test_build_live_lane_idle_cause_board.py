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

import build_live_lane_idle_cause_board as idle_board


class LiveLaneIdleCauseBoardTests(unittest.TestCase):
    def test_build_payload_classifies_hold_waiting_and_contract_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            dashboard = {
                "rows": [
                    {
                        "lane": "live_btcusd_m15_warp_941781",
                        "kind": "live_crypto",
                        "status": "ok",
                        "evidence_basis": "intentional_hold_live",
                        "operator_posture": "wait_profitable_unwind",
                        "managed_open_count": 125,
                        "display_close_count": 331,
                        "fresh_session_booked_usd": 99.69,
                        "fresh_session_usd_per_hour": 31.39,
                        "runner_status": "positive_only_hold_active",
                    },
                    {
                        "lane": "live_solusd_m15_warp_v2_941891",
                        "kind": "live_crypto",
                        "status": "ok",
                        "evidence_basis": "thin_live_sample",
                        "operator_posture": "wait_more_sample",
                        "managed_open_count": 0,
                        "display_close_count": 0,
                        "fresh_session_booked_usd": 0.0,
                        "fresh_session_usd_per_hour": 0.0,
                        "runner_status": "",
                    },
                    {
                        "lane": "live_gbpusd_m1_snake_hybrid_941797",
                        "kind": "live_fx",
                        "status": "ok",
                        "evidence_basis": "contract_invalid_live",
                        "operator_posture": "fix_contract_before_recycle",
                        "managed_open_count": 12,
                        "display_close_count": 5,
                        "fresh_session_booked_usd": 0.0,
                        "fresh_session_usd_per_hour": 0.0,
                        "runner_status": "live_contract_friction_invalid",
                    },
                ]
            }
            crypto = {
                "rows": [
                    {
                        "lane": "live_solusd_m15_warp_v2_941891",
                        "execution_read": "waiting_for_first_fill",
                        "nearest_side": "SELL",
                        "nearest_gap_steps": 0.345,
                    }
                ]
            }
            (reports / "live_lane_dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")
            (reports / "live_crypto_trigger_proximity_board.json").write_text(json.dumps(crypto), encoding="utf-8")

            with patch.object(idle_board, "LIVE_LANE_DASHBOARD_JSON", reports / "live_lane_dashboard.json"), patch.object(
                idle_board, "CRYPTO_TRIGGER_JSON", reports / "live_crypto_trigger_proximity_board.json"
            ):
                payload = idle_board.build_payload()

        causes = {row["lane"]: row["idle_cause"] for row in payload["rows"]}
        self.assertEqual(causes["live_btcusd_m15_warp_941781"], "intentional_hold")
        self.assertEqual(causes["live_solusd_m15_warp_v2_941891"], "waiting_for_first_fill")
        self.assertEqual(causes["live_gbpusd_m1_snake_hybrid_941797"], "contract_friction_invalid")


if __name__ == "__main__":
    unittest.main()
