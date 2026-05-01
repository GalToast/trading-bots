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

import build_live_crypto_first_fill_pressure_board as pressure


class LiveCryptoFirstFillPressureBoardTests(unittest.TestCase):
    def test_build_payload_sorts_by_gap_over_atr_not_gap_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()

            proximity_payload = {
                "generated_at": "2026-04-17T22:31:36+00:00",
                "summary": {"nearest_symbol": "ADAUSD"},
                "rows": [
                    {
                        "symbol": "ADAUSD",
                        "lane": "live_adausd_m15_warp_941893",
                        "nearest_side": "SELL",
                        "nearest_gap_steps": 0.37,
                        "nearest_gap_px": 0.00185,
                        "bid": 0.2604,
                        "ask": 0.2614,
                        "execution_read": "waiting_for_first_fill",
                        "spread_gate_status": "admissible_now",
                    },
                    {
                        "symbol": "ETHUSD",
                        "lane": "live_ethusd_m5_warp_5_941890",
                        "nearest_side": "SELL",
                        "nearest_gap_steps": 0.912,
                        "nearest_gap_px": 4.56,
                        "bid": 2430.62,
                        "ask": 2437.48,
                        "execution_read": "waiting_for_first_fill",
                        "spread_gate_status": "admissible_now",
                    },
                ],
            }
            step_atr_payload = {
                "generated_at": "2026-04-17T22:31:36+00:00",
                "rows": [
                    {
                        "symbol": "ADAUSD",
                        "reference_atr": 0.00095,
                        "quality_band": "supra_atr_watch_for_overwide_contract",
                        "authority_status": "",
                    },
                    {
                        "symbol": "ETHUSD",
                        "reference_atr": 6.997857,
                        "quality_band": "sub_atr_danger",
                        "authority_status": "historical_proof_conflicts_with_current_control_truth",
                    },
                ],
            }
            (reports / "live_crypto_trigger_proximity_board.json").write_text(
                json.dumps(proximity_payload), encoding="utf-8"
            )
            (reports / "live_crypto_step_atr_quality_board.json").write_text(
                json.dumps(step_atr_payload), encoding="utf-8"
            )

            with patch.object(pressure, "REPORTS", reports), patch.object(
                pressure, "PROXIMITY_JSON", reports / "live_crypto_trigger_proximity_board.json"
            ), patch.object(
                pressure, "STEP_ATR_JSON", reports / "live_crypto_step_atr_quality_board.json"
            ):
                payload = pressure.build_payload()

        self.assertEqual(payload["summary"]["step_space_leader"], "ADAUSD")
        self.assertEqual(payload["summary"]["atr_space_leader"], "ETHUSD")
        self.assertEqual(payload["summary"]["atr_watch_order"], ["ETHUSD", "ADAUSD"])
        self.assertIn("Step-space and ATR-space do not currently agree", payload["current_read"][2])
        self.assertEqual(payload["rows"][0]["priority"], "authority_hygiene_before_retune")
        self.assertEqual(payload["rows"][1]["priority"], "nearest_in_steps_but_not_lightest_move")


if __name__ == "__main__":
    unittest.main()
