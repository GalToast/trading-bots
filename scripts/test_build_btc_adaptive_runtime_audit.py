from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import scripts.build_btc_adaptive_runtime_audit as audit


class BuildBtcAdaptiveRuntimeAuditTests(unittest.TestCase):
    def test_resolve_controller_shape_prefers_current_plan_recommendation(self) -> None:
        shape_id, shape = audit.resolve_controller_shape(
            {
                "symbols": {
                    "BTCUSD": {
                        "candidate_shapes": [
                            {"shape_id": "btcusd_regime_rangeatr_v1", "close": {"alpha": 1.0}},
                            {"shape_id": "btcusd_rangeatr_cash_harvest_v1", "close": {"alpha": 0.5}},
                        ]
                    }
                }
            },
            {"controller_recommendation": {"recommended_shape_id": "btcusd_rangeatr_cash_harvest_v1"}},
        )

        self.assertEqual(shape_id, "btcusd_rangeatr_cash_harvest_v1")
        self.assertEqual(shape["close"]["alpha"], 0.5)

    def test_resolve_controller_expected_step_prefers_plan(self) -> None:
        expected = audit.resolve_controller_expected_step(
            {"avg_range": 300.0, "range_atr_ratio": 1.25},
            {"adaptive_step_plan": {"step": 255.0}},
        )

        self.assertEqual(expected, 255.0)

    def test_resolve_controller_expected_step_uses_range_atr_formula_inputs(self) -> None:
        expected = audit.resolve_controller_expected_step(
            {"avg_range": 300.0, "range_atr_ratio": 1.25},
            None,
        )

        self.assertEqual(expected, 255.0)

    def test_resolve_controller_expected_step_returns_none_without_formula_inputs(self) -> None:
        expected = audit.resolve_controller_expected_step(
            {"current_atr": 500.0, "step_coeff": 0.8},
            None,
        )

        self.assertIsNone(expected)

    def test_runtime_truth_checks_fail_for_unsupervised_stale_runtime(self) -> None:
        runtime = {
            "enabled": True,
            "direct_live": True,
            "watchdog_group": "",
            "watchdog_status": "",
            "stale_after_seconds": 240,
            "runner_heartbeat_age_seconds": 1800.0,
        }
        checks = audit.build_runtime_truth_checks(runtime)
        indexed = {item["check_id"]: item for item in checks}
        self.assertEqual(indexed["runtime_presence"]["status"], "fail")
        self.assertEqual(indexed["runtime_freshness"]["status"], "fail")
        self.assertEqual(indexed["runtime_direct_live"]["status"], "warn")

    def test_runtime_truth_checks_pass_for_fresh_supervised_shadow(self) -> None:
        runtime = {
            "enabled": True,
            "direct_live": False,
            "watchdog_group": "crypto_watchdog",
            "watchdog_status": "ok",
            "stale_after_seconds": 240,
            "runner_heartbeat_age_seconds": 30.0,
        }
        checks = audit.build_runtime_truth_checks(runtime)
        indexed = {item["check_id"]: item for item in checks}
        self.assertEqual(indexed["runtime_presence"]["status"], "pass")
        self.assertEqual(indexed["runtime_freshness"]["status"], "pass")
        self.assertEqual(indexed["runtime_direct_live"]["status"], "pass")

    def test_runtime_truth_checks_warn_for_parked_stale_artifact(self) -> None:
        runtime = {
            "enabled": False,
            "direct_live": True,
            "watchdog_group": "",
            "watchdog_status": "",
            "stale_after_seconds": 240,
            "runner_heartbeat_age_seconds": 1800.0,
        }
        checks = audit.build_runtime_truth_checks(runtime)
        indexed = {item["check_id"]: item for item in checks}
        self.assertEqual(indexed["runtime_presence"]["status"], "warn")
        self.assertEqual(indexed["runtime_freshness"]["status"], "warn")
        self.assertEqual(indexed["runtime_direct_live"]["status"], "warn")

    def test_build_checks_uses_active_controller_shape_alpha(self) -> None:
        checks = audit.build_checks(
            {
                "step": 255.0,
                "raw_close_alpha": 0.5,
                "max_open_per_side": 6,
                "step_buy": None,
                "step_sell": None,
                "enabled": False,
                "direct_live": True,
                "watchdog_group": "",
                "watchdog_status": "",
                "stale_after_seconds": 240,
                "runner_heartbeat_age_seconds": 1800.0,
            },
            "btcusd_rangeatr_cash_harvest_v1",
            {"close": {"alpha": 0.5}},
            {"avg_range": 300.0, "range_atr_ratio": 1.25},
            {
                "adaptive_step_plan": {"step": 255.0},
                "controller_recommendation": {"recommended_shape_id": "btcusd_rangeatr_cash_harvest_v1"},
            },
            {"shapes": {"btc_m15_aggressive": {"step": 255.0, "raw_close_alpha": 0.5, "max_open_per_side": 6, "step_buy": 3, "step_sell": 9}}},
        )
        indexed = {item["check_id"]: item for item in checks}
        self.assertEqual(indexed["controller_shape"]["status"], "pass")
        self.assertEqual(indexed["controller_alpha"]["status"], "pass")

    def test_build_runtime_summary_reads_realized_state_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_root = audit.ROOT
            audit.ROOT = Path(tmp)
            reports = audit.ROOT / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "btc_state.json").write_text(
                json.dumps(
                    {
                        "symbols": {
                            "BTCUSD": {
                                "realized_closes": 8,
                                "realized_net_usd": 24.0,
                                "anchor_resets": 3,
                                "first_path_verdict": "green_and_monetized",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            try:
                runtime = audit.build_runtime_summary(
                    {
                        "name": "shadow_btcusd_m15_adaptive_regime",
                        "kind": "shadow_crypto",
                        "enabled": False,
                        "state_path": "reports/btc_state.json",
                        "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--timeframe", "M15", "--step", "405"],
                        "stale_after_seconds": 240,
                    },
                    {
                        "open_count": 0,
                        "runner_session_trade_closes": 0,
                        "runner_session_trade_realized_usd": 0.0,
                        "pre_start_state_carry_closes": 1,
                        "pre_start_state_carry_realized_usd": -17.77,
                        "runner_heartbeat_at": "",
                        "last_trade_event_at": "",
                        "state_last_write_at": "",
                        "watchdog_status": "stale",
                    },
                )
            finally:
                audit.ROOT = original_root

        self.assertEqual(runtime["realized_close_count"], 8)
        self.assertEqual(runtime["realized_net_usd"], 24.0)
        self.assertEqual(runtime["realized_avg_per_close"], 3.0)
        self.assertEqual(runtime["anchor_reset_count"], 3)
        self.assertEqual(runtime["first_path_verdict"], "green_and_monetized")


if __name__ == "__main__":
    unittest.main()
