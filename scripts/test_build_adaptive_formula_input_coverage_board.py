#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_formula_input_coverage_board as board


class BuildAdaptiveFormulaInputCoverageBoardTests(unittest.TestCase):
    def test_range_atr_formula_marks_fallback_when_only_atr_fields_exist(self) -> None:
        payload = board.build_payload(
            {
                "generated_at": "2026-04-16T05:00:00Z",
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "stage": "shadow_ready",
                        "recommended_shape_id": "btcusd_regime_rangeatr_v1",
                        "family": "raw",
                        "step_read": "range/ATR adaptive formula",
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:01:00Z",
                "symbols": [
                    {
                        "symbol": "BTCUSD",
                        "regime": "TRANSITION",
                        "current_atr": 493.2,
                        "step_coeff": 0.8,
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:02:00Z",
                "symbols": {
                    "BTCUSD": {
                        "candidate_shapes": [
                            {
                                "shape_id": "btcusd_regime_rangeatr_v1",
                                "family": "raw",
                                "step_method": {"kind": "range_atr_formula", "basis": "range_atr_ratio"},
                            }
                        ]
                    }
                }
            },
        )

        row = payload["rows"][0]
        self.assertEqual(row["verdict"], "fallback_only_current_atr_step_coeff")
        self.assertEqual(sorted(row["missing_fields"]), ["avg_range", "range_atr_ratio"])
        self.assertEqual(payload["source_details"][0]["surface_id"], "adaptive_lattice_proof_board")

    def test_atr_multiple_marks_ready_when_current_atr_present(self) -> None:
        payload = board.build_payload(
            {
                "generated_at": "2026-04-16T05:00:00Z",
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "stage": "shadow_ready",
                        "recommended_shape_id": "gbpusd_trend_harvest_v1",
                        "family": "raw",
                        "step_read": "ATR sell=0.5 buy=1.0",
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:01:00Z",
                "symbols": [
                    {
                        "symbol": "GBPUSD",
                        "regime": "WEAK_TREND",
                        "current_atr": 0.00113,
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:02:00Z",
                "symbols": {
                    "GBPUSD": {
                        "candidate_shapes": [
                            {
                                "shape_id": "gbpusd_trend_harvest_v1",
                                "family": "raw",
                                "step_method": {"kind": "atr_multiple_asymmetric", "basis": "atr"},
                            }
                        ]
                    }
                }
            },
        )

        self.assertEqual(payload["rows"][0]["verdict"], "atr_ready")

    def test_range_atr_formula_marks_true_ready_when_range_inputs_exist(self) -> None:
        payload = board.build_payload(
            {
                "generated_at": "2026-04-16T05:00:00Z",
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "stage": "shadow_ready",
                        "recommended_shape_id": "btcusd_regime_rangeatr_v1",
                        "family": "raw",
                        "step_read": "range/ATR adaptive formula",
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:01:00Z",
                "symbols": [
                    {
                        "symbol": "BTCUSD",
                        "regime": "STRONG_TREND",
                        "current_atr": 463.3,
                        "avg_range": 467.7,
                        "range_atr_ratio": 1.01,
                        "step_coeff": 1.5,
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:02:00Z",
                "symbols": {
                    "BTCUSD": {
                        "candidate_shapes": [
                            {
                                "shape_id": "btcusd_regime_rangeatr_v1",
                                "family": "raw",
                                "step_method": {"kind": "range_atr_formula", "basis": "range_atr_ratio"},
                            }
                        ]
                    }
                }
            },
        )

        row = payload["rows"][0]
        self.assertEqual(row["verdict"], "true_range_atr_ready")
        self.assertEqual(row["missing_fields"], [])

    def test_render_markdown_mentions_formula_debt(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "source_details": [
                    {
                        "surface_id": "adaptive_lattice_proof_board",
                        "path": "reports/adaptive_lattice_proof_board.json",
                        "generated_at": "2026-04-16T00:00:00+00:00",
                        "age_hours": 0.0,
                        "read": "fresh snapshot (0.0h old)",
                    }
                ],
                "leadership_read": ["one"],
                "summary": {
                    "symbol_count": 1,
                    "verdict_counts": {"fallback_only_current_atr_step_coeff": 1},
                    "formula_input_debt_symbols": ["BTCUSD"],
                },
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "stage": "shadow_ready",
                        "shape_id": "btcusd_regime_rangeatr_v1",
                        "step_kind": "range_atr_formula",
                        "step_read": "range/ATR adaptive formula",
                        "formula_basis": "range_atr_ratio",
                        "required_fields": ["current_atr", "avg_range", "range_atr_ratio"],
                        "present_fields": ["current_atr", "step_coeff"],
                        "missing_fields": ["avg_range", "range_atr_ratio"],
                        "fallback_fields_present": ["current_atr", "step_coeff"],
                        "verdict": "fallback_only_current_atr_step_coeff",
                        "rationale": "rationale",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive Formula Input Coverage Board", markdown)
        self.assertIn("fallback_only_current_atr_step_coeff", markdown)
        self.assertIn("BTCUSD", markdown)
        self.assertIn("Source Details", markdown)

    def test_blocked_row_marks_runtime_family_block(self) -> None:
        payload = board.build_payload(
            {
                "generated_at": "2026-04-16T05:00:00Z",
                "rows": [
                    {
                        "symbol": "USDJPY",
                        "stage": "blocked_runtime",
                        "recommended_shape_id": "",
                        "family": "",
                        "step_read": "-",
                        "status": "ok",
                    }
                ]
            },
            {
                "generated_at": "2026-04-16T05:01:00Z",
                "symbols": [{"symbol": "USDJPY", "current_atr": 0.14571, "regime": "STRONG_TREND", "step_coeff": 1.5}],
            },
            {"generated_at": "2026-04-16T05:02:00Z", "symbols": {}},
        )

        self.assertEqual(payload["rows"][0]["verdict"], "blocked_runtime_family")


if __name__ == "__main__":
    unittest.main()
