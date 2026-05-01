#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_m15_warp_restore_board as board


class BuildBTCM15WarpRestoreBoardTests(unittest.TestCase):
    def test_build_payload_uses_current_live_and_restore_candidate_truth(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            memory = root / "memory"
            reports = root / "reports"
            configs = root / "configs"
            memory.mkdir()
            reports.mkdir()
            configs.mkdir()

            (memory / "validated-edges.md").write_text(
                "\n".join(
                    [
                        "- M15 Warp SHADOW ($15 step): 311 closes, $4,829 net, 0 resets, ~ $1,015/hour",
                        "- **S+**: BTC M15 Warp LIVE ($75 step) — 159 closes, +$783/35c clean, $22.37/close",
                    ]
                ),
                encoding="utf-8",
            )
            (reports / "optimal_lattice_specs.json").write_text(
                json.dumps(
                    {
                        "symbols": {
                            "BTCUSD_M15": {
                                "expected_pnl_per_close": 15.0,
                                "expected_closes_per_hour": 2,
                                "expected_pnl_per_hour": 30.0,
                                "action": "DO NOT retune live lane. Launch new shadow with optimal geometry for comparison.",
                                "optimal": {
                                    "step_sell": 145,
                                    "step_buy": 289,
                                    "step_sell_x_atr": 0.5,
                                    "step_buy_x_atr": 1.0,
                                    "close_style": "all_profitable",
                                    "close_alpha": 0.5,
                                    "close_gap": 1,
                                    "max_open_per_side": 12,
                                    "max_floating_loss_usd": -15.0,
                                    "source": "atr_step_optimization.json",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (reports / "adaptive_btc_shadow_runner_plan.json").write_text(
                json.dumps(
                    {
                        "status": "manual_review_required",
                        "adaptive_step_plan": {"step": 850.34},
                        "warnings": ["adaptive_step_vs_baseline_ratio_high:56.69x"],
                    }
                ),
                encoding="utf-8",
            )
            (reports / "execution_monitor_report.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "lane": "live_btcusd_m15_warp_941781",
                                "close_count": 277,
                                "runner_session_trade_opens": 0,
                                "runner_session_trade_closes": 0,
                                "runner_session_trade_realized_usd": 0.0,
                                "pre_start_state_carry_closes": 277,
                                "pre_start_state_carry_realized_usd": 1248.75,
                                "anchor_resets": 249,
                                "anchor_resets_flat": 245,
                                "anchor_resets_risk": 4,
                                "next_buy_level": 74426.4,
                                "next_sell_level": 75009.81,
                                "quote_bid": 74914.5,
                                "quote_ask": 75094.52,
                                "notes": "pre_start_state_carry=277c/+1248.75",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "live_lane_dashboard.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "lane": "live_btcusd_m15_warp_941781",
                                "evidence_basis": "carry_weighted_live",
                                "operator_posture": "require_fresh_forward_sample",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (configs / "penetration_lattice_runner_registry.json").write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "live_btcusd_m15_warp_941781",
                                "state_path": "reports/penetration_lattice_live_btcusd_m15_warp_state.json",
                                "restart_args": [
                                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                                    "--step",
                                    "75",
                                    "--max-floating-loss-usd",
                                    "-3500.0",
                                ],
                            },
                            {
                                "name": "shadow_btcusd_m15_warp",
                                "enabled": False,
                                "pause_note": "KILLED 2026-04-14: 105 resets, -$242, step=$15 way too tight",
                                "restart_args": ["python", "--step", "15", "--max-open-per-side", "80", "--max-floating-loss-usd", "-15.0"],
                            },
                            {
                                "name": "shadow_btcusd_m15_warp_on20",
                                "enabled": False,
                                "pause_note": "toxic_0.08x_atr_micro_step_killed_20260415",
                                "restart_args": ["python", "--step", "20", "--max-open-per-side", "60", "--max-floating-loss-usd", "-15.0"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "penetration_lattice_live_btcusd_m15_warp_state.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "step": 75.0,
                            "step_buy": 75.0,
                            "step_sell": 75.0,
                            "max_floating_loss_usd": -15.0,
                        },
                        "symbols": {
                            "BTCUSD": {
                                "base_step_px": 75.0,
                                "max_floating_loss_usd": -15.0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            original_root = board.ROOT
            original_reports = board.REPORTS
            original_memory = board.MEMORY
            original_validated = board.VALIDATED_EDGES_MD
            original_optimal = board.OPTIMAL_SPECS_JSON
            original_adaptive = board.ADAPTIVE_PLAN_JSON
            original_execution = board.EXECUTION_MONITOR_JSON
            original_dashboard = board.LIVE_LANE_DASHBOARD_JSON
            original_registry = board.REGISTRY_JSON
            original_restore_state = board.RESTORE_STATE_PATH
            original_restore_event = board.RESTORE_EVENT_PATH
            try:
                board.ROOT = root
                board.REPORTS = reports
                board.MEMORY = memory
                board.VALIDATED_EDGES_MD = memory / "validated-edges.md"
                board.OPTIMAL_SPECS_JSON = reports / "optimal_lattice_specs.json"
                board.ADAPTIVE_PLAN_JSON = reports / "adaptive_btc_shadow_runner_plan.json"
                board.EXECUTION_MONITOR_JSON = reports / "execution_monitor_report.json"
                board.LIVE_LANE_DASHBOARD_JSON = reports / "live_lane_dashboard.json"
                board.REGISTRY_JSON = configs / "penetration_lattice_runner_registry.json"
                board.RESTORE_STATE_PATH = reports / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json"
                board.RESTORE_EVENT_PATH = reports / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_events.jsonl"
                payload = board.build_payload()
            finally:
                board.ROOT = original_root
                board.REPORTS = original_reports
                board.MEMORY = original_memory
                board.VALIDATED_EDGES_MD = original_validated
                board.OPTIMAL_SPECS_JSON = original_optimal
                board.ADAPTIVE_PLAN_JSON = original_adaptive
                board.EXECUTION_MONITOR_JSON = original_execution
                board.LIVE_LANE_DASHBOARD_JSON = original_dashboard
                board.REGISTRY_JSON = original_registry
                board.RESTORE_STATE_PATH = original_restore_state
                board.RESTORE_EVENT_PATH = original_restore_event

        self.assertEqual(payload["historical_best_shadow"]["step"], 15.0)
        self.assertEqual(payload["historical_best_shadow"]["closes"], 311)
        self.assertEqual(payload["current_live_runtime"]["runtime_step"], 75.0)
        self.assertEqual(payload["current_live_runtime"]["anchor_resets_risk"], 4)
        self.assertEqual(payload["restore_candidate"]["step_sell"], 145.0)
        self.assertEqual(payload["restore_candidate"]["step_buy"], 289.0)
        self.assertIn("--fresh-start", payload["restore_candidate"]["command"])
        self.assertIn("--step-buy", payload["restore_candidate"]["command"])
        self.assertIn("--shared-price-max-age-ms", payload["restore_candidate"]["command"])
        shared_idx = payload["restore_candidate"]["command"].index("--shared-price-max-age-ms")
        self.assertEqual(payload["restore_candidate"]["command"][shared_idx + 1], "0")
        self.assertEqual(payload["restore_candidate"]["adaptive_plan_status"], "manual_review_required")
        self.assertEqual(payload["retired_shadow_restore_baselines"][0]["lane"], "shadow_btcusd_m15_warp")


if __name__ == "__main__":
    unittest.main()
