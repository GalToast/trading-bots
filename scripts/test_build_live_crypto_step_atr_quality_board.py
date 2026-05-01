#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_live_crypto_step_atr_quality_board as board


class BuildLiveCryptoStepAtrQualityBoardTests(unittest.TestCase):
    def test_build_payload_classifies_ratio_bands(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True, exist_ok=True)

            proximity = {
                "rows": [
                    {
                        "lane": "live_ethusd_m5_warp_5_941890",
                        "symbol": "ETHUSD",
                        "step_px": 5.0,
                        "status": "ok",
                        "operator_posture": "wait_more_sample",
                        "execution_read": "waiting_for_first_fill",
                        "nearest_gap_steps": 1.5,
                        "spread_ratio": 1.1,
                        "max_entry_spread_ratio": 1.4,
                    },
                    {
                        "lane": "live_solusd_m15_warp_v2_941891",
                        "symbol": "SOLUSD",
                        "step_px": 0.42,
                        "status": "ok",
                        "operator_posture": "wait_more_sample",
                        "execution_read": "waiting_for_first_fill",
                        "nearest_gap_steps": 1.1,
                        "spread_ratio": 0.54,
                        "max_entry_spread_ratio": 0.65,
                    },
                    {
                        "lane": "live_adausd_m15_warp_941893",
                        "symbol": "ADAUSD",
                        "step_px": 0.005,
                        "status": "ok",
                        "operator_posture": "wait_more_sample",
                        "execution_read": "waiting_for_first_fill",
                        "nearest_gap_steps": 0.33,
                        "spread_ratio": 0.16,
                        "max_entry_spread_ratio": 0.9,
                    },
                ]
            }
            atr_reference = {
                "generated_at": "2026-04-14T15:24:55Z",
                "atr_data": [
                    {"symbol": "ETHUSD", "tf": "M5", "ATR": 7.0, "current_x_ATR": 0.429},
                    {"symbol": "SOLUSD", "tf": "M15", "ATR": 0.25, "current_x_ATR": 1.68},
                    {"symbol": "ADAUSD", "tf": "M15", "ATR": 0.001, "current_x_ATR": 5.0},
                ],
            }
            regime = {
                "symbols": [
                    {"symbol": "ETHUSD", "current_atr": 16.0},
                    {"symbol": "SOLUSD", "current_atr": 0.3},
                ]
            }
            eth_comparison = {
                "comparison_status": "ready_for_clean_control_vs_variant",
                "historical_baseline": {
                    "archival_vs_current_conflict": True,
                    "archival_probe_read": "historical shelf only; do not treat as current control truth",
                },
                "leadership_read": [
                    "ETH M5 remains the right first offensive-closure pilot, but archival step5 shelf evidence must stay archival context only."
                ],
            }

            (reports / "live_crypto_trigger_proximity_board.json").write_text(json.dumps(proximity), encoding="utf-8")
            (reports / "atr_step_optimization.json").write_text(json.dumps(atr_reference), encoding="utf-8")
            (reports / "regime_classification_live.json").write_text(json.dumps(regime), encoding="utf-8")
            (reports / "eth_m5_first_pilot_comparison_board.json").write_text(json.dumps(eth_comparison), encoding="utf-8")

            with (
                patch.object(board, "ROOT", root),
                patch.object(board, "REPORTS", reports),
                patch.object(board, "PROXIMITY_JSON", reports / "live_crypto_trigger_proximity_board.json"),
                patch.object(board, "ATR_OPTIMIZATION_JSON", reports / "atr_step_optimization.json"),
                patch.object(board, "REGIME_LIVE_JSON", reports / "regime_classification_live.json"),
                patch.object(board, "ETH_M5_COMPARISON_JSON", reports / "eth_m5_first_pilot_comparison_board.json"),
                patch.object(board, "utc_now_iso", return_value="2026-04-17T22:20:00+00:00"),
            ):
                payload = board.build_payload()
                markdown = board.build_markdown(payload)

        rows = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(rows["ETHUSD"]["quality_band"], "sub_atr_danger")
        self.assertEqual(rows["ETHUSD"]["authority_status"], "historical_proof_conflicts_with_current_control_truth")
        self.assertEqual(rows["ETHUSD"]["next_action"], "comparison_hygiene_before_live_regrade")
        self.assertEqual(rows["SOLUSD"]["quality_band"], "preferred_atr_band")
        self.assertEqual(rows["ADAUSD"]["quality_band"], "supra_atr_watch_for_overwide_contract")
        self.assertEqual(payload["summary"]["sub_atr_danger_count"], 1)
        self.assertEqual(payload["summary"]["preferred_band_count"], 1)
        self.assertEqual(payload["summary"]["supra_atr_watch_count"], 1)
        self.assertIn("checked-in symbol-specific and timeframe-specific crypto ATR surface", markdown)
        self.assertIn("historical_proof_conflicts_with_current_control_truth", markdown)
        self.assertIn("sub_atr_danger", markdown)

    def test_parked_rows_are_excluded_from_step_atr_board(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True, exist_ok=True)

            proximity = {
                "rows": [
                    {
                        "lane": "live_ethusd_m5_warp_5_941890",
                        "symbol": "ETHUSD",
                        "step_px": 14.0,
                        "status": "paused",
                        "evidence_basis": "decommissioned_or_parked",
                        "operator_posture": "leave_paused",
                        "execution_read": "waiting_for_first_fill",
                        "nearest_gap_steps": 0.6,
                        "spread_ratio": 0.4,
                        "max_entry_spread_ratio": 0.0,
                    },
                    {
                        "lane": "live_solusd_m15_warp_v2_941891",
                        "symbol": "SOLUSD",
                        "step_px": 0.42,
                        "status": "ok",
                        "evidence_basis": "thin_live_sample",
                        "operator_posture": "wait_more_sample",
                        "execution_read": "waiting_for_first_fill",
                        "nearest_gap_steps": 1.1,
                        "spread_ratio": 0.54,
                        "max_entry_spread_ratio": 0.65,
                    },
                ]
            }
            atr_reference = {
                "generated_at": "2026-04-14T15:24:55Z",
                "atr_data": [
                    {"symbol": "ETHUSD", "tf": "M5", "ATR": 7.0, "current_x_ATR": 2.0},
                    {"symbol": "SOLUSD", "tf": "M15", "ATR": 0.25, "current_x_ATR": 1.68},
                ],
            }
            regime = {"symbols": [{"symbol": "SOLUSD", "current_atr": 0.3}]}
            eth_comparison = {"comparison_status": "parked"}

            (reports / "live_crypto_trigger_proximity_board.json").write_text(json.dumps(proximity), encoding="utf-8")
            (reports / "atr_step_optimization.json").write_text(json.dumps(atr_reference), encoding="utf-8")
            (reports / "regime_classification_live.json").write_text(json.dumps(regime), encoding="utf-8")
            (reports / "eth_m5_first_pilot_comparison_board.json").write_text(json.dumps(eth_comparison), encoding="utf-8")

            with (
                patch.object(board, "ROOT", root),
                patch.object(board, "REPORTS", reports),
                patch.object(board, "PROXIMITY_JSON", reports / "live_crypto_trigger_proximity_board.json"),
                patch.object(board, "ATR_OPTIMIZATION_JSON", reports / "atr_step_optimization.json"),
                patch.object(board, "REGIME_LIVE_JSON", reports / "regime_classification_live.json"),
                patch.object(board, "ETH_M5_COMPARISON_JSON", reports / "eth_m5_first_pilot_comparison_board.json"),
                patch.object(board, "utc_now_iso", return_value="2026-04-17T23:40:00+00:00"),
            ):
                payload = board.build_payload()

        self.assertEqual(payload["summary"]["row_count"], 1)
        self.assertEqual([row["symbol"] for row in payload["rows"]], ["SOLUSD"])


if __name__ == "__main__":
    unittest.main()
