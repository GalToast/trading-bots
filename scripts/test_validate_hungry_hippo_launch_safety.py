#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_hungry_hippo_launch_safety as safety


class ValidateHungryHippoLaunchSafetyTests(unittest.TestCase):
    def test_crypto_runner_flags_and_step_floor_fail(self) -> None:
        payload = {
            "name": "shadow_eth_bad",
            "kind": "shadow_crypto",
            "enabled": True,
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol", "ETHUSD",
                "--timeframe", "M5",
                "--step", "4.0",
                "--raw-close-alpha", "0.3",
                "--max-floating-loss-usd", "-15.0",
                "--escape-hatch",
                "--escape-max-bars", "15",
                "--escape-max-loss", "3.0",
                "--escape-cut-count", "1",
                "--escape-max-cut-loss", "5.0",
            ],
            "hungry_hippo_metadata": {"validation_status": "shadow_rebuild_only"},
        }
        gate_row = {"deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST"}

        row = safety.evaluate_config(Path("configs/hungry_hippo_eth_bad_shadow.json"), payload, gate_row)
        self.assertEqual(row["verdict"], "fail")
        self.assertIn("crypto_runner_has_fx_only_escape_flags", row["hard_fail_reasons"])
        self.assertIn("crypto_step_below_5_floor", row["hard_fail_reasons"])
        self.assertIn("gate_hard_block_but_current_control_is_shadow_only", row["advisory_reasons"])

    def test_fx_floor_and_alpha_are_enforced(self) -> None:
        payload = {
            "name": "shadow_gbp_bad",
            "kind": "shadow_fx",
            "enabled": True,
            "restart_args": [
                "scripts/live_penetration_lattice_tick_shadow.py",
                "--symbol", "GBPUSD",
                "--timeframe", "M15",
                "--step-sell", "0.00029",
                "--step-buy", "0.00058",
                "--raw-close-alpha", "0.2",
                "--max-floating-loss-usd", "-15.0",
                "--escape-hatch",
                "--escape-max-bars", "10",
                "--escape-max-loss", "3.0",
            ],
        }

        row = safety.evaluate_config(Path("configs/hungry_hippo_gbp_bad_shadow.json"), payload, None)
        self.assertEqual(row["verdict"], "fail")
        self.assertIn("alpha_below_floor", row["hard_fail_reasons"])
        self.assertIn("fx_step_below_floor", row["hard_fail_reasons"])

    def test_gate_context_stays_advisory_when_config_contract_is_clean(self) -> None:
        payload = {
            "name": "shadow_btc_ok",
            "kind": "shadow_crypto",
            "enabled": True,
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol", "BTCUSD",
                "--timeframe", "M5",
                "--step", "200",
                "--raw-close-alpha", "1.0",
                "--max-floating-loss-usd", "-15.0",
                "--escape-hatch",
                "--escape-max-bars", "12",
                "--escape-max-loss", "5.0",
            ],
        }
        gate_row = {"deployment_verdict": "manual_review"}

        row = safety.evaluate_config(Path("configs/hungry_hippo_btc_ok_shadow.json"), payload, gate_row)
        self.assertEqual(row["verdict"], "research_only")
        self.assertEqual(row["hard_fail_reasons"], [])
        self.assertIn("symbol_requires_manual_review", row["advisory_reasons"])

    def test_cleared_fx_shadow_contract_can_pass(self) -> None:
        payload = {
            "name": "shadow_usdchf_ok",
            "kind": "shadow_fx",
            "enabled": False,
            "restart_args": [
                "scripts/live_penetration_lattice_tick_shadow.py",
                "--symbol", "USDCHF",
                "--timeframe", "M15",
                "--step", "0.00052571",
                "--step-sell", "0.00052571",
                "--step-buy", "0.00052571",
                "--raw-close-alpha", "0.3",
                "--max-floating-loss-usd", "-15.0",
                "--escape-hatch",
                "--escape-max-bars", "20",
                "--escape-max-loss", "1.0",
                "--escape-cut-count", "1",
                "--escape-max-cut-loss", "5.0",
            ],
        }
        gate_row = {"deployment_verdict": "cleared_for_shadow_discussion"}

        row = safety.evaluate_config(Path("configs/hungry_hippo_usdchf_m15_extreme_shadow.json"), payload, gate_row)
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual(row["hard_fail_reasons"], [])
        self.assertEqual(row["advisory_reasons"], [])

    def test_low_priced_crypto_uses_symbol_specific_step_floor(self) -> None:
        payload = {
            "name": "shadow_xrp_ok",
            "kind": "shadow_crypto",
            "enabled": False,
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol", "XRPUSD",
                "--timeframe", "M15",
                "--step", "0.00655179",
                "--raw-close-alpha", "0.7",
                "--max-floating-loss-usd", "-15.0",
                "--escape-hatch",
                "--escape-max-bars", "12",
                "--escape-max-loss", "2.0",
            ],
        }
        gate_row = {"deployment_verdict": "cleared_for_shadow_discussion"}

        row = safety.evaluate_config(Path("configs/hungry_hippo_xrpusd_m15_breakout_shadow.json"), payload, gate_row)
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual(row["hard_fail_reasons"], [])
        self.assertEqual(row["advisory_reasons"], [])

    def test_build_payload_counts_blocking_enabled_failures(self) -> None:
        config_payloads = [
            (
                Path("configs/a.json"),
                {
                    "name": "a",
                    "kind": "shadow_crypto",
                    "enabled": True,
                    "restart_args": [
                        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                        "--symbol", "ETHUSD",
                        "--timeframe", "M5",
                        "--step", "4",
                        "--raw-close-alpha", "0.3",
                        "--max-floating-loss-usd", "-15.0",
                        "--escape-hatch",
                        "--escape-max-bars", "10",
                        "--escape-max-loss", "3.0",
                    ],
                },
            ),
            (
                Path("configs/b.json"),
                {
                    "name": "b",
                    "kind": "shadow_crypto",
                    "enabled": False,
                    "restart_args": [
                        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                        "--symbol", "BTCUSD",
                        "--timeframe", "M5",
                        "--step", "200",
                        "--raw-close-alpha", "1.0",
                        "--max-floating-loss-usd", "-15.0",
                        "--escape-hatch",
                        "--escape-max-bars", "12",
                        "--escape-max-loss", "5.0",
                    ],
                },
            ),
        ]

        built = safety.build_payload(config_payloads, {"rows": []})
        self.assertEqual(built["summary"]["blocking_enabled_config_count"], 1)
        self.assertEqual(built["summary"]["verdict_counts"]["fail"], 1)
        self.assertEqual(built["summary"]["launch_contract_verdict_counts"]["fail"], 1)

    def test_live_config_scope_is_now_included(self) -> None:
        self.assertEqual(safety.config_scope(Path("configs/hungry_hippo_eurusd_live.json")), "live_surface")

    def test_live_surface_is_research_only_not_missing_restart_fail(self) -> None:
        payload = {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "geometry": {"step": 0.00029, "step_buy": 0.00029, "step_sell": 0.00029},
            "close": {"alpha": 0.2},
            "risk": {"max_floating_loss_usd": -15.0},
        }

        row = safety.evaluate_config(Path("configs/hungry_hippo_eurusd_live.json"), payload, None)
        self.assertEqual(row["verdict"], "research_only")
        self.assertNotIn("missing_restart_args", row["hard_fail_reasons"])
        self.assertNotIn("alpha_below_floor", row["hard_fail_reasons"])
        self.assertNotIn("fx_step_below_floor", row["hard_fail_reasons"])
        self.assertIn("live_surface_not_launch_contract", row["advisory_reasons"])
        self.assertIn("profile_alpha_below_launch_floor", row["advisory_reasons"])
        self.assertIn("profile_fx_step_below_launch_floor", row["advisory_reasons"])


if __name__ == "__main__":
    unittest.main()
